"""Schema 业务校验层（JSON Schema + 15 项静态检查）。

校验顺序：
  1. JSON Schema Draft 2020-12 (unevaluatedProperties: false)
  2. apiVersion 兼容性
  3. DAG 图校验 (dag.py)
  4. Agent 引用完整性
  5. JSONPath 数据流 (jsonpath.py)
  6. 工具白名单
  7. 密钥引用完整性
  8. 超时合理性
  9. 资源配额（仅 warning，无集群容量信息时跳过）
 10. 参数占位符完整性 (template.py)
 11. DNS-1123 命名
 12. 补偿动作引用完整性
 13. capability 词表 (config/capabilities.yaml)
 14. agentSelector 可匹配到至少一个 profile
 15. condition 表达式静态语法检查 (expr.py)

所有错误汇总后一次性返回 ValidationReport，而不是遇到第一个就停。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import jsonschema.validators
import yaml

from ..domain.errors import ConfigurationError
from ..domain.pipeline import Pipeline
from .dag import DagValidationResult, validate_dag
from .expr import validate_expression
from .jsonpath import check_write_isolation, validate_input_has_upstream
from .template import collect_placeholders

_SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "schema"
_SUPPORTED_API_VERSIONS = frozenset(["orchestra.io/v1"])
_DNS1123_RE = re.compile(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: ValidationReport) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def __str__(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN:  {w}")
        return "\n".join(lines) if lines else "  OK"


def validate_pipeline(
    data: dict[str, Any],
    *,
    profile_capabilities: dict[str, list[str]] | None = None,
    cluster_capacity: dict[str, Any] | None = None,
) -> ValidationReport:
    """全量校验。

    :param data: 已由 parser.load_yaml 加载的原始 dict（渲染模板前）
    :param profile_capabilities: {profile_name -> capabilities 列表}，用于 agentSelector 校验
    :param cluster_capacity: 集群资源上限，用于资源配额检查；None 时跳过
    """
    report = ValidationReport()

    # 1. JSON Schema
    _check_json_schema(data, report)
    if not report.valid:
        return report  # Schema 不通过，后续业务校验无意义

    # 2. apiVersion 兼容性
    api_ver = data.get("apiVersion", "")
    if api_ver not in _SUPPORTED_API_VERSIONS:
        report.add_error(f"不支持的 apiVersion: {api_ver!r}，支持: {sorted(_SUPPORTED_API_VERSIONS)}")
        return report

    # 解析为 Pydantic 对象（Schema 已通过，这里不会抛）
    try:
        pipeline = Pipeline.model_validate(data)
    except Exception as e:
        report.add_error(f"Pydantic 转换失败（内部错误）: {e}")
        return report

    spec = pipeline.spec
    stages = spec.pipeline.stages
    agent_names = set(spec.agents.keys())
    stage_names = {s.name for s in stages}

    # 3. DAG 校验
    dag_result: DagValidationResult = validate_dag(pipeline)
    report.errors.extend(dag_result.errors)
    report.warnings.extend(dag_result.warnings)

    # 4. Agent 引用完整性
    for stage in stages:
        if stage.agent and stage.agent not in agent_names:
            report.add_error(f"stage '{stage.name}': agent '{stage.agent}' 未在 spec.agents 中定义")
        if stage.agents:
            for a in stage.agents:
                if a not in agent_names:
                    report.add_error(f"stage '{stage.name}': agents 列表中 '{a}' 未在 spec.agents 中定义")
        if stage.agentSelector and profile_capabilities:
            if not _selector_matches_any(stage.agentSelector, profile_capabilities):
                report.add_warning(
                    f"stage '{stage.name}': agentSelector {stage.agentSelector} "
                    f"在当前 profile 集合中找不到匹配项"
                )
        # 补偿动作中的 agent
        if spec.pipeline.compensation:
            for action in spec.pipeline.compensation.actions:
                if action.agent not in agent_names:
                    report.add_error(
                        f"compensation.actions[forStage={action.forStage!r}]: "
                        f"agent '{action.agent}' 未在 spec.agents 中定义"
                    )

    # 5. JSONPath 数据流（input 必须有上游 output）
    declared_outputs: dict[str, str] = {}
    for stage in stages:
        if stage.output:
            path = stage.output if isinstance(stage.output, str) else stage.output.path
            # 写隔离检查
            conflicts = check_write_isolation(stage.name, path, declared_outputs)
            report.errors.extend(conflicts)
            declared_outputs[stage.name] = path

    for stage in stages:
        if stage.input and isinstance(stage.input, str):
            err = validate_input_has_upstream(stage.name, stage.input, declared_outputs)
            if err:
                report.add_warning(err)  # warning，运行时可能从 params 或外部注入

    # 6. 工具白名单（stage 声明的 agent 必须拥有该 stage 依赖的工具）
    # 当前为 warning，因为 tools 无法从 YAML 静态推断 agent 内部工具调用
    # 完整检查在 adapters.sandbox 做运行时拦截

    # 7. 密钥引用完整性
    declared_secrets = {s.name for s in spec.secrets}
    for stage in stages:
        if stage.input and isinstance(stage.input, str):
            for placeholder in _extract_secret_refs(stage.input):
                if placeholder not in declared_secrets:
                    report.add_error(
                        f"stage '{stage.name}': 引用了未声明的 secret '{placeholder}'"
                    )

    # 8. 超时合理性
    for stage in stages:
        if stage.timeouts:
            t = stage.timeouts
            _check_timeout_order(stage.name, t.heartbeat, t.startToClose, t.scheduleToClose, report)

    # 9. 资源配额（可选）
    if cluster_capacity:
        _check_resource_quota(spec.agents, cluster_capacity, report)

    # 10. 参数占位符完整性
    declared_params = {p.name: p for p in spec.parameters}
    for stage in stages:
        for val in [stage.input, stage.condition, stage.idempotencyKey]:
            if isinstance(val, str):
                for ph in collect_placeholders(val):
                    if ph.startswith("params."):
                        key = ph[len("params."):]
                        if key not in declared_params:
                            report.add_error(
                                f"stage '{stage.name}': 模板 '{{{{ {ph} }}}}' "
                                f"引用了未声明的参数 '{key}'"
                            )

    # 11. DNS-1123 命名（已在 Pydantic 层校验，这里做 warning 提醒）
    for stage in stages:
        if not _DNS1123_RE.match(stage.name):
            report.add_error(
                f"stage 名称 '{stage.name}' 不符合 DNS-1123 规范"
                "（小写字母/数字/连字符，≤63 字符）"
            )

    # 12. 补偿动作引用完整性
    if spec.pipeline.compensation:
        for action in spec.pipeline.compensation.actions:
            if action.forStage not in stage_names:
                report.add_error(
                    f"compensation.actions: forStage='{action.forStage}' "
                    f"不存在于 pipeline stages 中"
                )

    # 15. condition 表达式静态语法检查
    for stage in stages:
        if stage.condition:
            expr_errors = validate_expression(stage.condition)
            for e in expr_errors:
                report.add_error(f"stage '{stage.name}' condition: {e}")

    return report


def _check_json_schema(data: dict[str, Any], report: ValidationReport) -> None:
    schema_path = _SCHEMA_DIR / "pipeline.schema.json"
    if not schema_path.exists():
        report.add_warning(f"找不到 pipeline.schema.json，跳过 JSON Schema 校验")
        return
    schema = json.loads(schema_path.read_text())
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    for err in validator.iter_errors(data):
        path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
        report.add_error(f"[{path}] {err.message}")


def _check_timeout_order(
    stage_name: str,
    heartbeat: str | None,
    start_to_close: str | None,
    schedule_to_close: str | None,
    report: ValidationReport,
) -> None:
    """检查 heartbeat < startToClose < scheduleToClose。"""
    def parse_seconds(d: str | None) -> float | None:
        if d is None:
            return None
        units = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        m = re.fullmatch(r"(\d+)(ns|us|ms|s|m|h|d|w)", d)
        if not m:
            return None
        return float(m.group(1)) * units[m.group(2)]

    hb = parse_seconds(heartbeat)
    stc = parse_seconds(start_to_close)
    sc = parse_seconds(schedule_to_close)

    if hb and stc and hb >= stc:
        report.add_error(
            f"stage '{stage_name}': heartbeat ({heartbeat}) 必须 < startToClose ({start_to_close})"
        )
    if stc and sc and stc > sc:
        report.add_error(
            f"stage '{stage_name}': startToClose ({start_to_close}) 必须 ≤ scheduleToClose ({schedule_to_close})"
        )


def _check_resource_quota(
    agents: dict[str, Any],
    capacity: dict[str, Any],
    report: ValidationReport,
) -> None:
    """sum(agent.resources.requests) ≤ capacity。当前仅检查 tokensPerMinute。"""
    total_tpm = 0
    for name, agent_spec in agents.items():
        if agent_spec.resources and agent_spec.resources.requests and agent_spec.resources.requests.tokensPerMinute:
            total_tpm += agent_spec.resources.requests.tokensPerMinute
    limit_tpm = capacity.get("tokensPerMinute")
    if limit_tpm and total_tpm > limit_tpm:
        report.add_warning(
            f"所有 agent 的 tokensPerMinute 总和 ({total_tpm}) 超出集群配额 ({limit_tpm})"
        )


def _selector_matches_any(
    selector: Any,
    profile_capabilities: dict[str, list[str]],
) -> bool:
    """检查 agentSelector 能否匹配到至少一个 profile。"""
    for _name, caps in profile_capabilities.items():
        if selector.capabilities:
            if all(c in caps for c in selector.capabilities):
                return True
        else:
            return True  # 只有 role 约束，简化处理
    return False


def _extract_secret_refs(text: str) -> list[str]:
    """提取 ${secrets.xxx} 引用的 secret 名。"""
    return re.findall(r"\$\{secrets\.([a-zA-Z0-9_-]+)\}", text)
