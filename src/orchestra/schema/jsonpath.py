"""JSONPath 引用解析 + State 写隔离校验。

约定（来自 design.md）：
- stage.input  是一个 JSONPath 表达式，指向全局 State 中的某个子树
- stage.output.path 是一个 JSONPath 表达式，stage 只能写自己的 output 路径
- 引擎在 Stage 完成时用 set_value / check_write_isolation 校验

写隔离规则：
  output path "$.code.patch" 表示 Stage "code" 只能写 state["code"]["patch"]
  任何 Stage 不得写其他 Stage 已声明的 output 路径
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.errors import SchemaViolation

# 简单 JSONPath 子集：$.a.b.c[0].d
_SIMPLE_JSONPATH_RE = re.compile(r"^\$(\.[a-zA-Z_][a-zA-Z0-9_]*|\[\d+\])*$")


def parse_path(expr: str) -> list[str | int]:
    """解析 JSONPath 表达式为路径段列表。

    仅支持简单路径 ``$.a.b.c`` 和 ``$.a[0].b``。
    复杂表达式（过滤器、通配符）视为不可静态分析，返回空列表。

    :raises InvalidInput: 不合法的 JSONPath 语法
    """
    if not expr.startswith("$"):
        return []
    if not _SIMPLE_JSONPATH_RE.match(expr):
        return []  # 复杂表达式，静态分析跳过
    segments: list[str | int] = []
    rest = expr[1:]  # 去掉 $
    for part in re.split(r"(?=\.|\[)", rest):
        part = part.lstrip(".")
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            segments.append(int(part[1:-1]))
        else:
            segments.append(part)
    return segments


def get_value(state: dict[str, Any], path: str) -> Any:
    """从 State 中取值，路径不存在时返回 None。"""
    segments = parse_path(path)
    if not segments:
        return None
    current: Any = state
    for seg in segments:
        if isinstance(seg, int):
            if not isinstance(current, list) or seg >= len(current):
                return None
            current = current[seg]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(seg)
        if current is None:
            return None
    return current


def set_value(state: dict[str, Any], path: str, value: Any) -> None:
    """向 State 写入值。自动创建中间节点（dict）。

    :raises SchemaViolation: 路径写入时发现类型冲突
    """
    segments = parse_path(path)
    if not segments:
        raise SchemaViolation(f"无法写入无效路径: {path!r}")
    current: Any = state
    for seg in segments[:-1]:
        if isinstance(seg, int):
            if not isinstance(current, list):
                raise SchemaViolation(f"路径 {path!r} 中间段期望 list，得到 {type(current).__name__}")
            while len(current) <= seg:
                current.append({})
            current = current[seg]
        else:
            if not isinstance(current, dict):
                raise SchemaViolation(f"路径 {path!r} 中间段期望 dict，得到 {type(current).__name__}")
            if seg not in current:
                current[seg] = {}
            current = current[seg]
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise SchemaViolation(f"路径 {path!r} 末端期望 list")
        while len(current) <= last:
            current.append(None)
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise SchemaViolation(f"路径 {path!r} 末端期望 dict")
        current[last] = value


def check_write_isolation(
    stage_name: str,
    output_path: str,
    declared_outputs: dict[str, str],
) -> list[str]:
    """检查 output_path 是否与其他 Stage 声明的 output 冲突。

    :param stage_name: 当前 stage 名
    :param output_path: 当前 stage 要写的路径
    :param declared_outputs: {stage_name -> output_path} 所有已声明的 stage 输出
    :return: 冲突描述列表（空 = 无冲突）
    """
    errors: list[str] = []
    my_segs = parse_path(output_path)
    for other_name, other_path in declared_outputs.items():
        if other_name == stage_name:
            continue
        other_segs = parse_path(other_path)
        if not my_segs or not other_segs:
            continue
        if _paths_overlap(my_segs, other_segs):
            errors.append(
                f"stage '{stage_name}' 的 output '{output_path}' "
                f"与 stage '{other_name}' 的 output '{other_path}' 路径冲突"
            )
    return errors


def validate_input_has_upstream(
    stage_name: str,
    input_path: str,
    declared_outputs: dict[str, str],
) -> str | None:
    """检查 input_path 是否有某个上游 stage 的 output 覆盖它。

    :return: 错误信息或 None（无错误）
    """
    if not input_path.startswith("$."):
        return None
    if input_path.startswith("$.params."):
        return None  # 参数来自 PipelineRun.spec.parameters，不需要上游
    for _name, out_path in declared_outputs.items():
        if out_path == input_path or input_path.startswith(out_path + "."):
            return None
    return (
        f"stage '{stage_name}' 的 input '{input_path}' "
        f"没有对应的上游 stage output 写入"
    )


def _paths_overlap(a: list[str | int], b: list[str | int]) -> bool:
    """两条路径是否存在重叠（一条是另一条的前缀或相等）。"""
    min_len = min(len(a), len(b))
    return a[:min_len] == b[:min_len]
