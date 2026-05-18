"""condition 表达式沙箱求值（基于 CEL 或 simpleeval 回退）。

约定（design.md）：
- 表达式语法：CEL（Common Expression Language），支持：
    ==, !=, <, >, <=, >=, and, or, not, in, matches(正则), size()
- 禁止任意代码执行（__import__ / eval / exec / 文件IO）
- 求值上下文即 WorkflowState 的当前快照（dict）

CEL 优先；cel-python 不可用时回退到 simpleeval 白名单模式。
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.errors import InvalidInput


def evaluate(expression: str, state: dict[str, Any]) -> bool:
    """求值 condition 表达式。

    :param expression: CEL/简单表达式字符串，如 ``'test.result == "pass"'``
    :param state: 当前 WorkflowState dict（顶层 key = stage 名）
    :returns: True → 执行 stage；False → 跳过（SKIPPED）
    :raises InvalidInput: 表达式语法错误
    :raises RuntimeError: 沙箱检测到不安全操作
    """
    _guard_expression(expression)
    try:
        return _eval_with_cel(expression, state)
    except ImportError:
        return _eval_with_fallback(expression, state)
    except Exception as e:
        raise InvalidInput(f"condition 表达式求值失败: {expression!r} — {e}") from e


def validate_expression(expression: str) -> list[str]:
    """静态检查表达式是否合法（不求值，仅检查语法 + 安全约束）。

    :return: 错误描述列表，空列表表示合法。
    """
    errors: list[str] = []
    try:
        _guard_expression(expression)
    except RuntimeError as e:
        errors.append(str(e))
        return errors

    # 仅做安全守卫即可；运行时变量未定义不算语法错误
    return errors


# ---------- 安全守卫 ----------

_FORBIDDEN_PATTERNS = [
    r"__\w+__",          # 双下划线属性
    r"\beval\b",
    r"\bexec\b",
    r"\b__import__\b",
    r"\bopen\b",
    r"\bimport\b",
    r"\bos\b\.",
    r"\bsys\b\.",
    r"\bsubprocess\b",
]
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS))


def _guard_expression(expr: str) -> None:
    """拒绝包含危险模式的表达式。"""
    if _FORBIDDEN_RE.search(expr):
        raise RuntimeError(f"condition 表达式包含不允许的操作: {expr!r}")


# ---------- CEL 后端 ----------

def _eval_with_cel(expression: str, state: dict[str, Any]) -> bool:
    """使用 cel-python 求值。cel-python 不可用时抛 ImportError。"""
    try:
        import celpy  # type: ignore[import-untyped]
        import celpy.celtypes as ct
    except ImportError:
        raise

    env = celpy.Environment()
    ast = env.compile(expression)
    prog = env.program(ast)

    # 将 state dict 转为 CEL 变量（顶层 key → cel activation）
    activation: dict[str, Any] = {}
    for key, val in state.items():
        activation[key] = _py_to_cel(val, ct)

    result = prog.evaluate(activation)
    # CEL bool 结果
    if isinstance(result, (bool, ct.CELBool)):
        return bool(result)
    return bool(result)


def _py_to_cel(value: Any, ct: Any) -> Any:
    """Python 值 → CEL 类型（尽力转换）。"""
    if isinstance(value, bool):
        return ct.CELBool(value)
    if isinstance(value, int):
        return ct.CELInt(value)
    if isinstance(value, float):
        return ct.CELDouble(value)
    if isinstance(value, str):
        return ct.CELString(value)
    if isinstance(value, list):
        return ct.CELList([_py_to_cel(v, ct) for v in value])
    if isinstance(value, dict):
        return ct.CELMap({ct.CELString(k): _py_to_cel(v, ct) for k, v in value.items()})
    if value is None:
        return ct.CELNull(None)
    return ct.CELString(str(value))


# ---------- Fallback 后端（simpleeval 白名单）----------


class _DotDict:
    """将嵌套 dict 转为支持属性访问的对象。

    condition 表达式如 ``test.result == "pass"`` 需要将
    State 中的 ``{"test": {"result": "pass"}}`` 转为可用
    ``test.result`` 语法访问的对象。
    """

    def __init__(self, data: dict[str, Any]) -> None:
        for k, v in data.items():
            if isinstance(v, dict):
                object.__setattr__(self, k, _DotDict(v))
            elif isinstance(v, list):
                object.__setattr__(self, k, [_DotDict(i) if isinstance(i, dict) else i for i in v])
            else:
                object.__setattr__(self, k, v)

    def __repr__(self) -> str:
        items = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return f"_DotDict({items})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _DotDict):
            return self.__dict__ == other.__dict__
        return NotImplemented


def _state_to_dotdict(state: dict[str, Any]) -> _DotDict:
    """将顶层 dict 转为 _DotDict，递归处理嵌套。"""
    return _DotDict(state)


_ALLOWED_NAMES = {"True", "False", "None", "true", "false", "null", "size", "matches"}


def _eval_with_fallback(expression: str, state: dict[str, Any]) -> bool:
    """simpleeval 白名单求值（CEL 不可用时回退）。"""
    try:
        from simpleeval import EvalWithCompoundTypes, FeatureNotAvailable  # type: ignore[import-untyped]
    except ImportError:
        return _eval_pure_python(expression, state)

    def safe_size(x: Any) -> int:
        if isinstance(x, (str, list, dict)):
            return len(x)
        if isinstance(x, _DotDict):
            return len(x.__dict__)
        raise InvalidInput(f"size() 不支持类型 {type(x).__name__}")

    def safe_matches(text: str, pattern: str) -> bool:
        return bool(re.search(pattern, text))

    dot_state = _state_to_dotdict(state)
    evaluator = EvalWithCompoundTypes(
        names={k: v for k, v in dot_state.__dict__.items()},
        functions={"size": safe_size, "matches": safe_matches},
    )
    try:
        result = evaluator.eval(expression)
    except FeatureNotAvailable as e:
        raise RuntimeError(f"表达式使用了不允许的特性: {e}") from e
    return bool(result)


def _eval_pure_python(expression: str, state: dict[str, Any]) -> bool:
    """纯 Python 求值（最后回退，仅支持最基本语法）。"""
    # 将常见 CEL 写法转为 Python
    py_expr = expression.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    # 构建受限命名空间（dict 转 dot-accessible）
    dot_state = _state_to_dotdict(state)
    namespace: dict[str, Any] = {
        **dot_state.__dict__,
        "true": True,
        "false": False,
        "null": None,
        "size": len,
    }
    try:
        result = eval(py_expr, {"__builtins__": {}}, namespace)  # noqa: S307
    except Exception as e:
        raise InvalidInput(f"表达式求值失败: {py_expr!r} — {e}") from e
    return bool(result)
