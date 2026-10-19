"""集成测试：Review Gate（评审门禁）闭环。

覆盖 design.md §"Review Gate（评审门禁）"的三条关键路径：

1. ``verdict == "pass"`` → 门禁直接通过，不触发修复
2. ``verdict == "fail"`` → 按 owner 路由 issue 给修复 Agent → 重跑 retest → 重审 → pass
3. ``maxIterations`` 耗尽且 ``onMaxReached == "fail"`` → Workflow 失败

使用 temporalio in-process WorkflowEnvironment（时间跳跃），无需外部 Server。
标记 ``@pytest.mark.integration``；设 ``RECORD_FIXTURES=1`` 时录制 replay fixture。
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

pytestmark = pytest.mark.integration

FIXTURES_DIR = Path(__file__).parent.parent / "replay" / "fixtures"


def _temporal_available() -> bool:
    try:
        import temporalio.testing  # noqa: F401

        return True
    except ImportError:
        return False


skip_no_temporal = pytest.mark.skipif(not _temporal_available(), reason="temporalio not installed")


def _get_data_converter():
    try:
        from temporalio.contrib.pydantic import pydantic_data_converter  # type: ignore

        return pydantic_data_converter
    except ImportError:
        from temporalio.converter import DataConverter

        return DataConverter.default


def _init_stores(tmp_path: Path) -> None:
    from orchestra.observability.audit import init_audit_writer
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.state.idempotency import init_store

    init_store("sqlite", db_path=str(tmp_path / "idem.db"))
    init_artifact_store(str(tmp_path / "artifacts"))
    init_audit_writer(str(tmp_path / "audits.db"))


async def _make_worker(env, task_queue: str = "test-queue"):
    from orchestra.worker.registry import build_worker

    return build_worker(env.client, task_queue)


# ──────────────────────────────────────────────
# 被测 Pipeline：code → test → quality-gate(reviewGate)
# ──────────────────────────────────────────────

REVIEW_GATE_PIPELINE = """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: review-gate-it
  namespace: default
  version: 1.0.0
spec:
  agents:
    walnut:
      role: developer
      capabilities: [python]
      mcpEndpoint: "mcp://localhost:18761"
      tools: [file_read, file_write]
    almond:
      role: tester
      capabilities: [python, pytest]
      mcpEndpoint: "mcp://localhost:18762"
      tools: [pytest_run]
    blueberry:
      role: chat
      capabilities: [review, analyze]
      mcpEndpoint: "mcp://localhost:18768"
      tools: [file_read, submit_result]
  pipeline:
    stages:
      - name: code
        agent: walnut
        input: "$.params.spec"
        output: "$.code"
      - name: test
        agent: almond
        dependsOn: [code]
        input: "$.code"
        output: "$.test"
      - name: quality-gate
        dependsOn: [test]
        reviewGate:
          agent: blueberry
          input: "$.test"
          maxIterations: 2
          onMaxReached: fail
          outputSchema:
            type: object
            required: [verdict]
            properties:
              verdict: {type: string, enum: [pass, fail]}
          routing:
            tester: almond
            developer: walnut
          retest: [test]
  parameters:
    - name: spec
      type: string
      required: true
"""

PASS_RESULT = {"verdict": "pass", "confidence": 0.95, "issues": []}

FAIL_WITH_TESTER_ISSUE = {
    "verdict": "fail",
    "confidence": 0.4,
    "summary": "覆盖率不足",
    "issues": [
        {
            "id": "R1",
            "severity": "P1",
            "owner": "tester",
            "problem": "新增分支缺少测试",
            "suggestion": "补充边界用例",
        }
    ],
}


async def _run_gate(
    tmp_path: Path,
    review_sequence: list[dict],
    fixture_name: str | None = None,
) -> tuple[dict, dict, dict]:
    """跑一遍 review-gate pipeline，返回 (workflow_result, registry, state)。

    :param review_sequence: blueberry（review agent）按调用次序返回的结果
    :param fixture_name: 非 None 且 RECORD_FIXTURES=1 时录制 replay fixture
    """
    import yaml

    from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow

    _init_stores(tmp_path)
    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    registry = build_registry(
        profiles,
        use_mock=True,
        mock_outputs={
            "walnut": {"patch": "def f(): pass"},
            "almond": {"result": "pass", "coverage": 92.0},
        },
        mock_output_sequences={"blueberry": review_sequence},
    )

    pipeline = parse_pipeline(yaml.safe_load(REVIEW_GATE_PIPELINE))
    wf_id = "test-review-gate-001"

    from temporalio.testing import WorkflowEnvironment

    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=_get_data_converter()) as env,
        await _make_worker(env),
    ):
        result = await env.client.execute_workflow(
            PipelineWorkflow.run,
            PipelineRunInput(
                pipeline_dict=pipeline.model_dump(by_alias=True),
                run_id="run-review-gate",
                params={"spec": "实现登录功能"},
            ),
            id=wf_id,
            task_queue="test-queue",
            execution_timeout=timedelta(seconds=60),
        )
        if fixture_name:
            # 必须在 env 关闭前 await
            await _maybe_record_fixture(env, wf_id, fixture_name)

    return result, registry, result.get("state", {})


async def _maybe_record_fixture(env: object, workflow_id: str, fixture_name: str) -> None:
    """RECORD_FIXTURES=1 时把 Workflow History 存为 replay fixture。"""
    if not os.environ.get("RECORD_FIXTURES"):
        return

    try:
        handle = env.client.get_workflow_handle(workflow_id)  # type: ignore[attr-defined]
        history = await handle.fetch_history()
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURES_DIR / f"{fixture_name}.json").write_text(history.to_json())
        print(f"\n  [fixture] saved → {fixture_name}.json")
    except Exception as e:
        print(f"\n  [fixture] skip: {e}")


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────


@skip_no_temporal
@pytest.mark.asyncio
async def test_review_gate_passes_without_fix(tmp_path):
    """verdict=pass → 门禁通过；review 只调用一次，修复 Agent 不被触发。"""
    result, registry, state = await _run_gate(
        tmp_path, [PASS_RESULT], fixture_name="synthetic_review_gate_pass"
    )

    assert result["phase"] == "Succeeded"
    assert state["quality-gate"]["verdict"] == "pass"
    assert state["test"]["result"] == "pass"
    assert registry["blueberry"].call_count == 1


@skip_no_temporal
@pytest.mark.asyncio
async def test_review_gate_fail_routes_fix_and_retests_then_passes(tmp_path):
    """verdict=fail → 路由修复 + 重跑 retest → 重审 pass。"""
    result, registry, state = await _run_gate(
        tmp_path,
        [FAIL_WITH_TESTER_ISSUE, PASS_RESULT],
        fixture_name="synthetic_review_gate_loop",
    )

    assert result["phase"] == "Succeeded"
    assert state["quality-gate"]["verdict"] == "pass"
    # review agent 被调用 2 次（第一轮 fail + 第二轮 pass）
    assert registry["blueberry"].call_count == 2
    # 修复 Agent（tester→almond）被执行：test 初次 + retest + issue 修复
    assert registry["almond"].call_count >= 3


@skip_no_temporal
@pytest.mark.asyncio
async def test_review_gate_exhausted_max_iterations_fails(tmp_path):
    """maxIterations 耗尽且 onMaxReached=fail → Workflow 失败。"""
    from temporalio.client import WorkflowFailureError

    with pytest.raises(WorkflowFailureError):
        await _run_gate(tmp_path, [FAIL_WITH_TESTER_ISSUE])
