"""``{{ params.* }}`` / ``{{ inputs.* }}`` 占位符渲染。

约定（来自 design.md）：
- 语法：``{{ 表达式 }}``，支持变量引用和简单过滤器
- 渲染必须在 JSON Schema 校验**之后**进行（先校验结构，后注入值）
- 未声明的参数引用直接报 InvalidInput，不要静默跳过

支持的过滤器：
  | sha256  → 对字符串值计算 SHA-256 十六进制摘要
  | upper   → 转大写
  | lower   → 转小写
  | default("x") → 值为空时回退

安全约束：模板表达式不执行任意 Python 代码（禁止 eval / exec / __import__）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..domain.errors import InvalidInput

_TEMPLATE_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def render(template: str, context: dict[str, Any]) -> str:
    """渲染模板字符串。

    :param template: 含 ``{{ }}`` 占位符的字符串
    :param context: 变量字典，支持嵌套键（如 ``params.target_env``）
    :raises InvalidInput: 占位符引用的变量未在 context 中声明
    """

    def replace_match(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        return _eval_expr(expr, context)

    return _TEMPLATE_RE.sub(replace_match, template)


def render_dict(data: Any, context: dict[str, Any]) -> Any:
    """递归渲染 dict / list / string 中的所有占位符。"""
    if isinstance(data, str):
        return render(data, context)
    if isinstance(data, dict):
        return {k: render_dict(v, context) for k, v in data.items()}
    if isinstance(data, list):
        return [render_dict(item, context) for item in data]
    return data


def collect_placeholders(template: str) -> list[str]:
    """提取模板中所有占位符表达式（不渲染）。"""
    return [m.group(1).strip() for m in _TEMPLATE_RE.finditer(template)]


def validate_placeholders(
    template: str,
    declared_params: dict[str, Any],
) -> list[str]:
    """静态校验模板中 ``{{ params.* }}`` 引用是否在 ``declared_params`` 中声明。

    :return: 错误信息列表
    """
    errors: list[str] = []
    for expr in collect_placeholders(template):
        var_name = _extract_var_name(expr)
        if var_name and var_name.startswith("params."):
            param_key = var_name[len("params."):]
            if param_key not in declared_params:
                errors.append(f"占位符 '{{{{ {expr} }}}}' 引用了未声明的参数 '{param_key}'")
    return errors


# ---------- 内部实现 ----------

def _eval_expr(expr: str, context: dict[str, Any]) -> str:
    """求值单个占位符表达式（支持变量 + 过滤器管道）。"""
    parts = [p.strip() for p in expr.split("|")]
    var_expr = parts[0]
    filters = parts[1:]

    # 尝试解析变量；若有 default 过滤器，允许变量缺失
    try:
        value = _resolve_var(var_expr, context)
    except InvalidInput:
        # 没有 default 过滤器时才向上抛
        has_default = any(f.strip().startswith("default") for f in filters)
        if not has_default:
            raise
        value = None  # 由 default 过滤器决定最终值

    for f in filters:
        value = _apply_filter(f, value, expr)

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _resolve_var(var_expr: str, context: dict[str, Any]) -> Any:
    """解析变量路径（如 ``params.target_env``）。"""
    if not _SAFE_IDENT_RE.match(var_expr.split("(")[0]):
        raise InvalidInput(f"模板变量表达式不合法: {var_expr!r}")

    keys = var_expr.split(".")
    current: Any = context
    for key in keys:
        if not isinstance(current, dict):
            raise InvalidInput(
                f"模板变量 '{var_expr}' 解析失败：'{key}' 的父节点不是 dict"
            )
        if key not in current:
            raise InvalidInput(
                f"模板变量 '{var_expr}' 未在上下文中找到（missing key '{key}'）"
            )
        current = current[key]
    return current


def _extract_var_name(expr: str) -> str | None:
    """从表达式中提取变量名部分（去掉过滤器）。"""
    parts = expr.split("|")
    var_part = parts[0].strip()
    if _SAFE_IDENT_RE.match(var_part):
        return var_part
    return None


def _apply_filter(filter_expr: str, value: Any, original_expr: str) -> Any:
    """应用单个过滤器。"""
    fname = filter_expr.split("(")[0].strip()

    if fname == "sha256":
        s = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        return hashlib.sha256(s.encode()).hexdigest()

    if fname == "upper":
        return str(value).upper()

    if fname == "lower":
        return str(value).lower()

    if fname == "default":
        # default("fallback")
        m = re.match(r'default\("(.*)"\)', filter_expr)
        if m:
            return value if value not in (None, "", [], {}) else m.group(1)
        m2 = re.match(r"default\('(.*)'\)", filter_expr)
        if m2:
            return value if value not in (None, "", [], {}) else m2.group(1)
        raise InvalidInput(f"过滤器 default 语法错误: {filter_expr!r}")

    raise InvalidInput(
        f"模板表达式 '{original_expr}' 使用了未知过滤器: '{fname}'"
    )
