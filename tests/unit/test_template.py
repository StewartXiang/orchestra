"""单元测试：模板渲染"""
import pytest
from orchestra.schema.template import render, validate_placeholders

def test_simple_render():
    assert render("env={{ params.env }}", {"params": {"env": "prod"}}) == "env=prod"

def test_sha256_filter():
    result = render("{{ inputs.task | sha256 }}", {"inputs": {"task": "hello"}})
    import hashlib
    assert result == hashlib.sha256(b"hello").hexdigest()

def test_default_filter():
    assert render("{{ params.x | default(\"fallback\") }}", {"params": {}}) == "fallback"

def test_undeclared_param_error():
    with pytest.raises(Exception, match="missing key"):
        render("{{ params.missing }}", {"params": {}})

def test_validate_placeholders_ok():
    errs = validate_placeholders("{{ params.env }}", {"env": "staging"})
    assert errs == []

def test_validate_placeholders_missing():
    errs = validate_placeholders("{{ params.env }}", {})
    assert len(errs) == 1
    assert "env" in errs[0]
