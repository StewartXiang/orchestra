"""单元测试：表达式沙箱"""

import pytest

from orchestra.schema.expr import evaluate


def test_simple_eq_true():
    assert evaluate('result == "pass"', {"result": "pass"}) is True


def test_simple_eq_false():
    assert evaluate('result == "pass"', {"result": "fail"}) is False


def test_and():
    assert evaluate("a == 1 and b == 2", {"a": 1, "b": 2}) is True


def test_size():
    assert evaluate("size(bugs) > 0", {"bugs": [1, 2]}) is True


def test_security_guard_import():
    with pytest.raises((RuntimeError, Exception)):
        evaluate("__import__('os').system('ls')", {})


def test_security_guard_eval():
    with pytest.raises((RuntimeError, Exception)):
        evaluate("eval('1+1')", {})


# ── cel-python ≥0.4 兼容（CELBool/CELMap → BoolType/MapType）──


def test_nested_dict_field_selection():
    """嵌套 dict 字段访问（旧实现依赖 CELMap，新版本已改名）。"""
    assert evaluate('test.result == "fail"', {"test": {"result": "fail"}}) is True
    assert evaluate('test.result == "fail"', {"test": {"result": "pass"}}) is False


def test_or_and_not_operators():
    """DSL 承诺支持 and/or/not，需归一为 CEL 的 &&/||/!。"""
    assert evaluate("a == 1 or b == 9", {"a": 1, "b": 2}) is True
    assert evaluate("not (a == 2)", {"a": 1}) is True
    assert evaluate("a == 1 or b == 2 and c == 3", {"a": 0, "b": 2, "c": 3}) is True


def test_operator_words_inside_string_literal_preserved():
    """字符串字面量内的 and/or/not 不能被改写。"""
    assert evaluate('name == "rock and roll"', {"name": "rock and roll"}) is True
    assert evaluate('note == "do not"', {"note": "do not"}) is True


def test_null_is_python_none():
    assert evaluate("flag == null", {"flag": None}) is True
    assert evaluate("flag != null", {"flag": None}) is False


def test_cel_bool_result_is_python_bool():
    """返回必须是真正的 bool（CEL 结果类型已从 CELBool 改名）。"""
    assert evaluate("a == 1", {"a": 1}) is True
    assert evaluate("a == 2", {"a": 1}) is False
