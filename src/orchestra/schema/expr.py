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
    r"__\w+__",  # 双下划线属性
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
    """使用 cel-python 求值。cel-python 不可用时抛 ImportError。

    cel-python 0.4+ 将运行时类型从 ``CELBool``/``CELList``/``CELMap`` 重命名为
    ``BoolType``/``ListType``/``MapType``（见 celpy.celtypes）。这里按「新名优先、
    旧名兜底、都没有则原样透传」解析，兼容 0.1.x ~ 0.5.x，避免依赖升级再次打断
    condition 求值。

    CEL 原生逻辑运算符是 ``&&`` / ``||`` / ``!``，而 DSL（design.md）允许
    ``and`` / ``or`` / ``not``，故编译前先归一化（跳过字符串字面量内部）。
    """
    try:
        import celpy  # type: ignore[import-untyped]
        import celpy.celtypes as ct
    except ImportError:
        raise

    env = celpy.Environment()
    ast = env.compile(_normalize_cel_operators(expression))
    prog = env.program(ast)

    types = _resolve_cel_types(ct)
    # 将 state dict 转为 CEL 变量（顶层 key → cel activation）
    activation: dict[str, Any] = {key: _py_to_cel(val, types) for key, val in state.items()}
    return bool(prog.evaluate(activation))


# 字符串字面量（用于在归一化逻辑运算符时跳过字面量内部）
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'")


def _normalize_cel_operators(expression: str) -> str:
    """把 DSL 的 ``and`` / ``or`` / ``not`` 归一为 CEL 的 ``&&`` / ``||`` / ``!``。

    只在字符串字面量之外替换，避免 ``"rock and roll"`` 被改写。
    """

    def _fix(segment: str) -> str:
        segment = re.sub(r"\band\b", "&&", segment)
        segment = re.sub(r"\bor\b", "||", segment)
        return re.sub(r"(?<![\w!])not\b", "!", segment)

    parts = _STRING_LITERAL_RE.split(expression)
    literals = _STRING_LITERAL_RE.findall(expression)
    result = _fix(parts[0])
    for literal, segment in zip(literals, parts[1:], strict=False):
        result += literal + _fix(segment)
    return result


# 逻辑类型 → (新名, 旧名)。新名指 cel-python 0.4+，旧名指 <=0.3 的 CEL* 命名。
_CEL_TYPE_NAMES: dict[str, tuple[str, str]] = {
    "bool": ("BoolType", "CELBool"),
    "int": ("IntType", "CELInt"),
    "double": ("DoubleType", "CELDouble"),
    "string": ("StringType", "CELString"),
    "list": ("ListType", "CELList"),
    "map": ("MapType", "CELMap"),
}


def _resolve_cel_types(ct: Any) -> dict[str, Any]:
    """解析当前 cel-python 版本的运行时类型类（缺失的键表示不转换）。"""
    resolved: dict[str, Any] = {}
    for key, names in _CEL_TYPE_NAMES.items():
        for name in names:
            cls = getattr(ct, name, None)
            if cls is not None:
                resolved[key] = cls
                break
    return resolved


def _wrap(cls: Any, value: Any) -> Any:
    """用 CEL 类型类包装值；类不可用时返回原值。"""
    if cls is None:
        return value
    try:
        return cls(value)
    except TypeError:
        return value  # 构造签名不匹配时退回原值，保持宽松兼容


def _py_to_cel(value: Any, types: dict[str, Any]) -> Any:
    """Python 值 → CEL 类型（尽力转换；类型类不存在时原样透传）。"""
    if isinstance(value, bool):
        return _wrap(types.get("bool"), value)
    if isinstance(value, int):
        return _wrap(types.get("int"), value)
    if isinstance(value, float):
        return _wrap(types.get("double"), value)
    if isinstance(value, str):
        return _wrap(types.get("string"), value)
    if isinstance(value, list):
        return _wrap(types.get("list"), [_py_to_cel(v, types) for v in value])
    if isinstance(value, dict):
        key_cls = types.get("string")
        key_of = key_cls if key_cls is not None else (lambda k: k)
        mapped = {key_of(k): _py_to_cel(v, types) for k, v in value.items()}
        return _wrap(types.get("map"), mapped)
    if value is None:
        return None  # CEL `null` 即 Python None，勿包装成 NullType()
    return _wrap(types.get("string"), str(value))


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
        names=dict(dot_state.__dict__.items()),
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
        result = eval(py_expr, {"__builtins__": {}}, namespace)
    except Exception as e:
        raise InvalidInput(f"表达式求值失败: {py_expr!r} — {e}") from e
    return bool(result)
