"""YAML 文件 → Pipeline / PipelineRun Pydantic 对象。

流程（单向，不可逆）：
  raw YAML str
  → yaml.safe_load (dict)
  → JSON Schema 校验  (validator.py)
  → Pydantic model_validate (Pipeline)
  → template 渲染 (template.py, 渲染发生在 schema 校验后)

本模块只负责"把 YAML 加载为 dict"和"把 dict 转为 Pipeline"；
所有业务校验（DAG、引用完整性等）在 validator.py 中进行。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..domain.errors import ConfigurationError, InvalidInput
from ..domain.pipeline import Pipeline, PipelineRun


def load_yaml(source: str | Path) -> dict[str, Any]:
    """从文件路径或 YAML 字符串加载为 dict。"""
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = source
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise InvalidInput(f"YAML 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise InvalidInput("YAML 根节点必须是 object（dict）")
    return data


def parse_pipeline(source: str | Path | dict[str, Any]) -> Pipeline:
    """解析 Pipeline 定义。

    :param source: 文件路径 / YAML 字符串 / 已解析的 dict
    :raises InvalidInput: YAML 语法错误或 Pydantic 校验失败
    """
    data = source if isinstance(source, dict) else load_yaml(source)  # type: ignore[arg-type]
    _assert_kind(data, "Pipeline")
    try:
        return Pipeline.model_validate(data)
    except ValidationError as e:
        raise InvalidInput(
            f"Pipeline schema 校验失败：{_fmt_validation_errors(e)}", details={"errors": e.errors()}
        ) from e


def parse_pipeline_run(source: str | Path | dict[str, Any]) -> PipelineRun:
    """解析 PipelineRun。"""
    data = source if isinstance(source, dict) else load_yaml(source)  # type: ignore[arg-type]
    _assert_kind(data, "PipelineRun")
    try:
        return PipelineRun.model_validate(data)
    except ValidationError as e:
        raise InvalidInput(
            f"PipelineRun schema 校验失败：{_fmt_validation_errors(e)}", details={"errors": e.errors()}
        ) from e


def pipeline_to_dict(pipeline: Pipeline) -> dict[str, Any]:
    """Pipeline → dict（可序列化为 YAML/JSON）。"""
    return json.loads(pipeline.model_dump_json(by_alias=True, exclude_none=True))


def _assert_kind(data: dict[str, Any], expected: str) -> None:
    kind = data.get("kind")
    if kind != expected:
        raise ConfigurationError(
            f"期望 kind={expected!r}，得到 {kind!r}"
        )


def _fmt_validation_errors(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:5]:  # 最多显示 5 条
        loc = " → ".join(str(x) for x in err["loc"])
        lines.append(f"  [{loc}] {err['msg']}")
    if len(e.errors()) > 5:
        lines.append(f"  ... 共 {len(e.errors())} 个错误")
    return "\n" + "\n".join(lines)
