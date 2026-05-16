"""集成测试：动态 for_each Stage + loop 受限循环。"""

from __future__ import annotations

import asyncio
import sys
import uuid
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


def _registry(outputs: dict):
    from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    build_registry(profiles, use_mock=True, mock_outputs=outputs)


DYNAMIC_PIPELINE = """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: dynamic-test
  namespace: default
spec:
  agents:
    blueberry:
      role: chat
      mcpEndpoint: "mcp://localhost:18768"
    walnut:
      role: developer
      mcpEndpoint: "mcp://localhost:18761"
  pipeline:
    stages:
      - name: diagnose
        agent: blueberry
        output:
          path: "$.diagnose"
        timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: fix-each
        dependsOn: [diagnose]
        dynamic:
          generator: for_each
          input: "$.diagnose.bugs"
          maxParallel: 2
          maxItems: 10
          onItemFailure: continue
          aggregateOutput: "$.fixes"
          template:
            name: "fix-bug-{{ item.id }}"
            agent: walnut
            input: "$.item"
            output: "$.fix"
            timeouts: {startToClose: 5m, heartbeat: 30s}
      - name: summary
        agent: blueberry
        dependsOn: [fix-each]
        output:
          path: "$.summary"
        timeouts: {startToClose: 5m, heartbeat: 30s}
  global:
    heartbeatInterval: 15s
    timeouts:
      workflowExecution: 1h
"""


@skip_no_temporal
@pytest.mark.asyncio
async def test_dynamic_for_each_no_items(tmp_path):
    """for_each 输入为空列表时，跳过并继续。"""
    from temporalio.testing import WorkflowEnvironment
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.worker.registry import build_worker

    _setup(tmp_path)
    _registry({
        "blueberry": {"bugs": [], "summary": "no bugs"},
        "walnut": {"fixed": True},
    })

    pipeline = parse_pipeline(yaml.safe_load(DYNAMIC_PIPELINE))
    pipeline_dict = pipeline.model_dump(by_alias=True)

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        async with build_worker(env.client, "dynamic-queue"):
            result = await env.client.execute_workflow(
                PipelineWorkflow.run,
                PipelineRunInput(pipeline_dict=pipeline_dict, run_id=uuid.uuid4().hex[:8], params={}),
                id=f"dynamic-test-{uuid.uuid4().hex[:8]}",
                task_queue="dynamic-queue",
            )

    assert result["phase"] == "Succeeded"


@skip_no_temporal
@pytest.mark.asyncio
async def test_dynamic_for_each_with_items(tmp_path):
    """for_each 处理 N 个 bug，结果聚合到 $.fixes。"""
    from temporalio.testing import WorkflowEnvironment
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.worker.registry import build_worker

    _setup(tmp_path)
    bugs = [{"id": i, "severity": "medium", "desc": f"bug-{i}"} for i in range(3)]
    _registry({
        "blueberry": {"bugs": bugs, "summary": "3 bugs fixed"},
        "walnut": {"fixed": True, "patch": "fix applied"},
    })

    pipeline = parse_pipeline(yaml.safe_load(DYNAMIC_PIPELINE))
    pipeline_dict = pipeline.model_dump(by_alias=True)

    async with await WorkflowEnvironment.start_time_skipping(data_converter=_get_dc()) as env:
        async with build_worker(env.client, "dynamic-queue", max_concurrent_activities=5):
            result = await env.client.execute_workflow(
                PipelineWorkflow.run,
                PipelineRunInput(pipeline_dict=pipeline_dict, run_id=uuid.uuid4().hex[:8], params={}),
                id=f"dynamic-items-{uuid.uuid4().hex[:8]}",
                task_queue="dynamic-queue",
            )

    assert result["phase"] == "Succeeded"
    # fixes 应该有 3 个结果
    state = result.get("state", {})
    fixes = state.get("fixes", [])
    assert isinstance(fixes, list)
    assert len(fixes) == 3
