"""单元测试：Schema 校验"""
import pytest, yaml
from orchestra.schema.validator import validate_pipeline

def load(path):
    return yaml.safe_load(open(path))

def test_minimal_valid():
    r = validate_pipeline(load("examples/minimal.pipeline.yaml"))
    assert r.valid

def test_game_dev_valid():
    r = validate_pipeline(load("examples/game-dev.pipeline.yaml"))
    assert r.valid

def test_missing_agent_ref():
    data = load("examples/minimal.pipeline.yaml")
    data["spec"]["pipeline"]["stages"][0]["agent"] = "nonexistent"
    r = validate_pipeline(data)
    assert not r.valid
    assert any("nonexistent" in e for e in r.errors)

def test_bad_api_version():
    data = load("examples/minimal.pipeline.yaml")
    data["apiVersion"] = "orchestra.io/v99"
    r = validate_pipeline(data)
    assert not r.valid

def test_timeout_order_violation():
    data = load("examples/minimal.pipeline.yaml")
    data["spec"]["pipeline"]["stages"][0]["timeouts"] = {
        "heartbeat": "60s",
        "startToClose": "30s",  # heartbeat > startToClose → 错误
    }
    r = validate_pipeline(data)
    assert not r.valid
