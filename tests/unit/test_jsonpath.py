"""单元测试：JSONPath 读写隔离"""
import pytest
from orchestra.schema.jsonpath import get_value, set_value, check_write_isolation, parse_path

def test_set_get_simple():
    state = {}
    set_value(state, "$.code.patch", "diff")
    assert get_value(state, "$.code.patch") == "diff"

def test_set_nested():
    state = {}
    set_value(state, "$.a.b.c", 42)
    assert state == {"a": {"b": {"c": 42}}}

def test_get_missing_returns_none():
    assert get_value({}, "$.x.y") is None

def test_write_isolation_conflict():
    declared = {"code": "$.code.patch", "other": "$.code.patch"}
    errors = check_write_isolation("code", "$.code.patch", declared)
    assert any("conflict" in e or "冲突" in e for e in errors)

def test_write_isolation_no_conflict():
    declared = {"code": "$.code.patch", "test": "$.test.result"}
    errors = check_write_isolation("code", "$.code.patch", declared)
    # code 与 test 路径不冲突
    assert errors == []

def test_parse_path_simple():
    assert parse_path("$.a.b") == ["a", "b"]

def test_parse_path_array():
    assert parse_path("$.items[0].name") == ["items", 0, "name"]
