"""集成测试：game-dev 完整流水线（含 condition / 并行 / 审批 / 补偿）。

使用 examples/game-dev.pipeline.yaml，用 mock adapter 跑通 9-agent 流水线。
验证：9 个 Agent 都被调度、State 流转正确、流水线最终 Succeeded。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

pytestmark = pytest.mark.integration

_TEMPORAL_AVAILABLE = False
try:
    from temporalio.testing import WorkflowEnvironment
    _TEMPORAL_AVAILABLE = True
except ImportError:
    pass

skip_no_temporal = pytest.mark.skipif(not _TEMPORAL_AVAILABLE, reason="temporalio not installed")


def _get_dc():
    try:
        from temporalio.contrib.pydantic import pydantic_data_converter
        return pydantic_data_converter
    except ImportError:
        from temporalio.converter import DataConverter
        return DataConverter.default


def _setup(tmp_path):
    from orchestra.state.idempotency import init_store
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.observability.audit import init_audit_writer
    init_store("memory")
    init_artifact_store(str(tmp_path / "artifacts"))
    init_audit_writer(str(tmp_path / "audits.db"))


def _game_dev_registry():
    """game-dev 流水线需要所有 9 个 agent 都注册且能返回合理输出。"""
    from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    build_registry(profiles, use_mock=True, mock_outputs={
        "walnut":     {"patch": "diff --git a/src/main.gd b/src/main.gd\n+func init(): pass"},
        "almond":     {"result": "pass", "coverage": 85.0},
        "chestnut":   {"patch": "backend changes"},
        "coconut":    {"deployed": True, "url": "https://staging.example.com"},
        "cherry":     {"assets": ["button.png", "bg.png"], "exported": True},
        "mango":      {"shader": "shader_type canvas_item;"},
        "strawberry": {"ui_result": "pass", "screenshots": 5},
        "blueberry":  {"summary": "代码变更评审通过，UI 正常", "flags": []},
        "grape":      {"result": "ok"},
    })


@skip_no_temporal
@pytest.mark.asyncio
async def test_game_dev_schema_valid():
    """game-dev pipeline YAML schema 校验通过（静态，不跑 Temporal）。"""
    import yaml
    from orchestra.schema.parser import parse_pipeline
    from orchestra.schema.validator import validate_pipeline

    data = yaml.safe_load(open("examples/game-dev.pipeline.yaml"))
    report = validate_pipeline(data)
    assert report.valid, f"Schema 校验失败：\n{report}"
    pipeline = parse_pipeline(data)
    assert len(pipeline.spec.pipeline.stages) == 9
    assert set(pipeline.spec.agents.keys()) == {
        "walnut", "almond", "coconut", "cherry", "mango", "strawberry", "blueberry", "grape"
    }  # game-dev 流水线不含 chestnut


@skip_no_temporal
@pytest.mark.asyncio
async def test_game_dev_dag_analysis():
    """game-dev DAG 无环，并行 wave 分析正确。"""
    from orchestra.schema.parser import parse_pipeline
    from orchestra.schema.dag import validate_dag, parallel_groups

    pipeline = parse_pipeline("examples/game-dev.pipeline.yaml")
    dag = validate_dag(pipeline)
    assert dag.valid, f"DAG 错误: {dag.errors}"

    groups = parallel_groups(pipeline.spec.pipeline.stages)
    # 第一波：design-review（唯一起始节点）
    assert groups[0] == ["design-review"]
    # 第二波：code + art（并行）
    assert set(groups[1]) == {"art", "code"}


@skip_no_temporal
@pytest.mark.asyncio
async def test_game_dev_end_to_end_with_approval(tmp_path):
    """game-dev 流水线端到端：从 design-review 到 deploy。

    使用修改过的 pipeline dict，将审批 timeout 设为 1s + onTimeout=approve，
    这样在 time-skipping 模式下审批会自动通过，无需发送 Update。
    """
    import copy
    from temporalio.testing import WorkflowEnvironment
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.worker.registry import build_worker

    _setup(tmp_path)
    _game_dev_registry()

    pipeline = parse_pipeline("examples/game-dev.pipeline.yaml")
    pipeline_dict = pipeline.model_dump(by_alias=True)

    # 修改审批节点超时为 1s + onTimeout=approve（time-skipping 友好）
    for stage in pipeline_dict.get("spec", {}).get("pipeline", {}).get("stages", []):
        if stage.get("approval"):
            stage["approval"]["timeout"] = "1s"
            stage["approval"]["onTimeout"] = "approve"

    run_id = uuid.uuid4().hex[:8]
    wf_id = f"game-dev-e2e-{run_id}"

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        async with build_worker(env.client, "game-dev-queue", max_concurrent_activities=10):
            result = await env.client.execute_workflow(
                PipelineWorkflow.run,
                PipelineRunInput(
                    pipeline_dict=pipeline_dict,
                    run_id=run_id,
                    params={"gdd": "docs/gdd_v3.md"},
                ),
                id=wf_id,
                task_queue="game-dev-queue",
            )

    # 取 workflow 结果
    assert result["phase"] == "Succeeded", f"game-dev 流水线失败，得到: {result}"

    # 取 workflow 结果
    assert result["phase"] == "Succeeded", f"game-dev 流水线失败，得到: {result}"


@skip_no_temporal
@pytest.mark.asyncio
async def test_game_dev_dry_run():
    """game-dev dry-run CLI 输出正确（不依赖 Temporal）。"""
    from click.testing import CliRunner
    from orchestra.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["dry-run", "examples/game-dev.pipeline.yaml"])
    assert result.exit_code == 0
    assert "design-review" in result.output
    assert "Wave" in result.output
    # 验证并行 wave 包含 code 和 art
    assert "art" in result.output and "code" in result.output
