"""单元测试：表达式沙箱"""
import pytest
from orchestra.schema.expr import evaluate, validate_expression

def test_simple_eq_true():
    assert evaluate('result == "pass"', {"result": "pass"}) is True

def test_simple_eq_false():
    assert evaluate('result == "pass"', {"result": "fail"}) is False

def test_and():
    assert evaluate('a == 1 and b == 2', {"a": 1, "b": 2}) is True

def test_size():
    assert evaluate('size(bugs) > 0', {"bugs": [1, 2]}) is True

def test_security_guard_import():
    with pytest.raises((RuntimeError, Exception)):
        evaluate("__import__('os').system('ls')", {})

def test_security_guard_eval():
    with pytest.raises((RuntimeError, Exception)):
        evaluate("eval('1+1')", {})
