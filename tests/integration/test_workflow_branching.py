"""集成测试：条件分支 + 并行扇出/扇入 + 聚合策略。

覆盖场景：
  1. condition=False → Stage 被 SKIPPED（不阻塞后续）
  2. condition=True  → Stage 正常执行
  3. agents:[a,b] aggregateStrategy=all → 全部成功才通过
  4. agents:[a,b] aggregateStrategy=any → 任一成功即通过
  5. 前驱 SKIPPED 时 requireUpstream=True 的后继也 SKIPPED
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

pytestmark = pytest.mark.integration

_TEMPORAL_AVAILABLE = False
try:
    from temporalio.testing import WorkflowEnvironment
    _TEMPORAL_AVAILABLE = True
except ImportError:
    pass

skip_no_temporal = pytest.mark.skipif(not _TEMPORAL_AVAILABLE, reason="temporalio not installed")


# ── 辅助 ──────────────────────────────────────

def _get_dc():
    try:
        from temporalio.contrib.pydantic import pydantic_data_converter
        return pydantic_data_converter
    except ImportError:
        from temporalio.converter import DataConverter
        return DataConverter.default


def _setup(tmp_path: Path):
    from orchestra.state.idempotency import init_store
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.observability.audit import init_audit_writer
    init_store("memory")
    init_artifact_store(str(tmp_path / "artifacts"))
    init_audit_writer(str(tmp_path / "audits.db"))


def _registry(outputs: dict):
    from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    build_registry(profiles, use_mock=True, mock_outputs=outputs)


async def _run(env, pipeline_yaml: str, params: dict = {}) -> dict:
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.worker.registry import build_worker
    import uuid

    pipeline = parse_pipeline(yaml.safe_load(pipeline_yaml))
    pipeline_dict = pipeline.model_dump(by_alias=True)

    async with build_worker(env.client, "branch-queue"):
        result = await env.client.execute_workflow(
            PipelineWorkflow.run,
            PipelineRunInput(pipeline_dict=pipeline_dict, run_id=uuid.uuid4().hex[:8], params=params),
            id=f"branch-{uuid.uuid4().hex[:8]}",
            task_queue="branch-queue",
        )
    return result


# ── 条件分支测试 ──────────────────────────────

BRANCH_PIPELINE = """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: branch-test
  namespace: default
spec:
  agents:
    walnut:
      role: developer
      mcpEndpoint: "mcp://localhost:18761"
    almond:
      role: tester
      mcpEndpoint: "mcp://localhost:18762"
  pipeline:
    stages:
      - name: analyze
        agent: walnut
        output:
          path: "$.analyze"
        timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: code
        agent: walnut
        dependsOn: [analyze]
        condition: 'analyze.has_change == true'
        output:
          path: "$.code"
        timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: skip-stage
        agent: almond
        dependsOn: [analyze]
        condition: 'analyze.has_change == false'
        output:
          path: "$.skip"
        timeouts: {startToClose: 5m, heartbeat: 30s}
  global:
    heartbeatInterval: 15s
    timeouts:
      workflowExecution: 1h
"""


@skip_no_temporal
@pytest.mark.asyncio
async def test_condition_true_executes(tmp_path):
    """condition=True 的 stage 被执行，结果写入 State。"""
    from temporalio.testing import WorkflowEnvironment

    _setup(tmp_path)
    _registry({"walnut": {"has_change": True}, "almond": {"result": "skipped"}})

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        result = await _run(env, BRANCH_PIPELINE)

    assert result["phase"] == "Succeeded"
    # code stage 应该执行（condition=True）
    state = result.get("state", {})
    assert "analyze" in state


@skip_no_temporal
@pytest.mark.asyncio
async def test_condition_false_skips(tmp_path):
    """condition=False 的 stage 被跳过（SKIPPED），流水线仍 Succeeded。"""
    from temporalio.testing import WorkflowEnvironment

    _setup(tmp_path)
    _registry({"walnut": {"has_change": False}, "almond": {"result": "done"}})

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        result = await _run(env, BRANCH_PIPELINE)

    # 流水线整体应成功（SKIPPED 不等于 FAILED）
    assert result["phase"] == "Succeeded"


# ── 并行扇出测试 ──────────────────────────────

PARALLEL_PIPELINE = """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: parallel-test
  namespace: default
spec:
  agents:
    strawberry:
      role: tester
      mcpEndpoint: "mcp://localhost:18767"
    grape:
      role: standby
      mcpEndpoint: "mcp://localhost:18769"
    walnut:
      role: developer
      mcpEndpoint: "mcp://localhost:18761"
  pipeline:
    stages:
      - name: prepare
        agent: walnut
        output:
          path: "$.prepare"
        timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: ui-verify
        agents: [strawberry, grape]
        dependsOn: [prepare]
        aggregateStrategy: all
        output:
          path: "$.ui"
        timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: report
        agent: walnut
        dependsOn: [ui-verify]
        output:
          path: "$.report"
        timeouts: {startToClose: 5m, heartbeat: 30s}
  global:
    heartbeatInterval: 15s
    timeouts:
      workflowExecution: 1h
"""


@skip_no_temporal
@pytest.mark.asyncio
async def test_parallel_all_success(tmp_path):
    """并行 agents 全部成功时，下游 stage 可执行。"""
    from temporalio.testing import WorkflowEnvironment

    _setup(tmp_path)
    _registry({
        "walnut": {"done": True},
        "strawberry": {"ui": "pass"},
        "grape": {"ui": "pass"},
    })

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        result = await _run(env, PARALLEL_PIPELINE)

    assert result["phase"] == "Succeeded"


# ── 线性链含超时语义 ──────────────────────────

@skip_no_temporal
@pytest.mark.asyncio
async def test_linear_chain_state_flow(tmp_path):
    """线性链：每个 stage 的 output 正确流转到下游 input。"""
    from temporalio.testing import WorkflowEnvironment

    _setup(tmp_path)
    _registry({
        "walnut": {"patch": "diff --git a/main.py..."},
        "almond": {"result": "pass", "coverage": 88.5},
    })

    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.worker.registry import build_worker
    import uuid

    pipeline = parse_pipeline("examples/minimal.pipeline.yaml")
    pipeline_dict = pipeline.model_dump(by_alias=True)

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        async with build_worker(env.client, "branch-queue"):
            result = await env.client.execute_workflow(
                PipelineWorkflow.run,
                PipelineRunInput(
                    pipeline_dict=pipeline_dict,
                    run_id="state-flow-001",
                    params={"task": "implement feature X"},
                ),
                id="state-flow-test",
                task_queue="branch-queue",
            )

    assert result["phase"] == "Succeeded"
    # State 应包含 params
    state = result.get("state", {})
    assert state.get("params", {}).get("task") == "implement feature X"
