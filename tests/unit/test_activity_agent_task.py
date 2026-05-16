"""单元测试：activities — mock Adapter 场景。

不依赖 Temporal：用 unittest.mock 替代 activity context。
覆盖：正常完成 / 幂等命中 / 错误分类。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.adapters.mock import MockAgentAdapter, MockBehavior
from orchestra.domain.state import TaskInput


# ──────────────────────────────────────────────
# Mock Temporal activity context
# ──────────────────────────────────────────────

class _FakeActivityInfo:
    workflow_id = "wf-test-123"
    activity_id = "act-001"
    attempt = 1
    heartbeat_details = []


def _mock_activity_ctx():
    """Patch temporalio.activity module for unit tests."""
    mock = MagicMock()
    mock.info.return_value = _FakeActivityInfo()
    mock.heartbeat = MagicMock()
    mock.is_cancelled = MagicMock(return_value=False)
    return mock


def _make_task(agent_name: str = "walnut") -> TaskInput:
    return TaskInput(
        workflow_id="wf-test-123",
        stage_name="code",
        agent_name=agent_name,
        role="developer",
        tools=["file_read"],
        input={"task": "implement feature"},
        idempotency_key="wf-test-123/act-001",
    )


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_agent_task_success(tmp_path):
    """正常执行：返回 StageOutput(success=True)。"""
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store, SQLiteIdempotencyStore

    init_store("sqlite", db_path=str(tmp_path / "idempotency.db"))
    from orchestra.adapters import registry as reg
    adapter = MockAgentAdapter("walnut", output={"patch": "diff"})
    reg._REGISTRY["walnut"] = adapter

    mock_act = _mock_activity_ctx()
    with patch("orchestra.activities.agent_task.activity", mock_act):
        result = await execute_agent_task(
            AgentTaskInput(task=_make_task(), stage_name="code", pipeline_name="test")
        )

    assert result.success
    assert result.stage_name == "code"
    assert result.output_value == {"patch": "diff"}
    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_execute_agent_task_idempotent(tmp_path):
    """重复调用：幂等键命中，不重复执行 Agent。"""
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    db = str(tmp_path / "idempotency.db")
    init_store("sqlite", db_path=db)

    from orchestra.adapters import registry as reg
    adapter = MockAgentAdapter("walnut", output={"patch": "cached"})
    reg._REGISTRY["walnut"] = adapter

    mock_act = _mock_activity_ctx()
    inp = AgentTaskInput(task=_make_task(), stage_name="code", pipeline_name="test")

    with patch("orchestra.activities.agent_task.activity", mock_act):
        r1 = await execute_agent_task(inp)
        r2 = await execute_agent_task(inp)  # same idempotency key

    assert r1.success and r2.success
    # adapter 只被调用一次
    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_execute_agent_task_failure(tmp_path):
    """Agent 失败：抛出异常，stage_failure 指标递增。"""
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("sqlite", db_path=str(tmp_path / "idempotency.db"))

    from orchestra.adapters import registry as reg
    adapter = MockAgentAdapter("walnut", behavior=MockBehavior.FAIL, error_message="boom")
    reg._REGISTRY["walnut"] = adapter

    mock_act = _mock_activity_ctx()
    with patch("orchestra.activities.agent_task.activity", mock_act):
        with pytest.raises(RuntimeError, match="boom"):
            await execute_agent_task(
                AgentTaskInput(task=_make_task(), stage_name="code", pipeline_name="test")
            )


@pytest.mark.asyncio
async def test_heartbeat_called(tmp_path):
    """正常执行时 activity.heartbeat 至少被调用一次。"""
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("sqlite", db_path=str(tmp_path / "idempotency2.db"))

    from orchestra.adapters import registry as reg
    adapter = MockAgentAdapter("walnut", output={"result": "ok"})
    reg._REGISTRY["walnut"] = adapter

    mock_act = _mock_activity_ctx()
    with patch("orchestra.activities.agent_task.activity", mock_act):
        await execute_agent_task(
            AgentTaskInput(task=_make_task(), stage_name="code", pipeline_name="test")
        )

    assert mock_act.heartbeat.call_count >= 1
