"""单元测试：DAG 校验"""
import pytest
from orchestra.schema.dag import validate_dag, parallel_groups, topological_order
from orchestra.schema.parser import parse_pipeline

@pytest.fixture
def minimal():
    return parse_pipeline("examples/minimal.pipeline.yaml")

@pytest.fixture
def game_dev():
    return parse_pipeline("examples/game-dev.pipeline.yaml")

def test_minimal_valid(minimal):
    r = validate_dag(minimal)
    assert r.valid
    assert r.topo_order == ["code", "test"]

def test_minimal_parallel_groups(minimal):
    groups = parallel_groups(minimal.spec.pipeline.stages)
    assert groups == [["code"], ["test"]]

def test_game_dev_valid(game_dev):
    r = validate_dag(game_dev)
    assert r.valid

def test_game_dev_first_wave(game_dev):
    groups = parallel_groups(game_dev.spec.pipeline.stages)
    assert groups[0] == ["design-review"]

def test_cycle_detection():
    """环检测：A→B→A"""
    import yaml
    from orchestra.schema.dag import _kahn_sort
    from orchestra.domain.pipeline import Stage
    stages = [
        Stage(name="a", dependsOn=["b"]),
        Stage(name="b", dependsOn=["a"]),
    ]
    _, err = _kahn_sort(stages)
    assert err is not None
    assert "环" in err

def test_orphan_warning():
    """孤儿节点：多于 1 个 stage 时孤立节点产生 warning"""
    from orchestra.schema.dag import validate_dag
    import yaml
    p = parse_pipeline("examples/minimal.pipeline.yaml")
    # 添加一个孤立 stage（无依赖无后继）
    from orchestra.domain.pipeline import Stage
    p.spec.pipeline.stages.append(Stage(name="orphan", agent="walnut"))
    r = validate_dag(p)
    assert any("orphan" in w for w in r.warnings)
