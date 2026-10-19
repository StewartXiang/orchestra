"""单元测试：JSONPath 读写隔离"""

from orchestra.schema.jsonpath import check_write_isolation, get_value, parse_path, set_value


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


def test_parse_path_hyphenated_key():
    """Stage 名常含连字符（quality-gate / review-loop），必须可解析。"""
    assert parse_path("$.quality-gate") == ["quality-gate"]
    assert parse_path("$.review-loop.fixes.R1") == ["review-loop", "fixes", "R1"]


def test_set_get_hyphenated_key():
    """连字符键的写入不得被静默丢弃。"""
    state = {}
    set_value(state, "$.quality-gate", {"verdict": "pass"})
    assert state == {"quality-gate": {"verdict": "pass"}}
    assert get_value(state, "$.quality-gate.verdict") == "pass"


def test_parse_path_quoted_key():
    assert parse_path("$['my key']") == ["my key"]
    assert parse_path('$["a.b"]') == ["a.b"]


def test_parse_path_complex_returns_empty():
    """过滤器/通配符等复杂表达式不可静态分析，返回空列表。"""
    assert parse_path("$.items[?(@.x)]") == []
    assert parse_path("$.a.*") == []
    assert parse_path("not-a-path") == []
