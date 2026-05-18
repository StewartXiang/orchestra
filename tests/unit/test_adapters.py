"""单元测试：adapters — sandbox + mock 行为。"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.adapters.mock import MockAgentAdapter, MockBehavior
from orchestra.adapters.sandbox import Sandbox
from orchestra.domain.enums import Role
from orchestra.domain.errors import ToolNotAllowed
from orchestra.domain.state import TaskInput


# ── Sandbox ──────────────────────────────────

def test_sandbox_allowed():
    sb = Sandbox(allowed_tools=["file_read", "git_commit"])
    # 不应抛
    sb.check_tool("file_read")
    sb.check_tool("git_commit")


def test_sandbox_denied():
    sb = Sandbox(allowed_tools=["file_read"])
    with pytest.raises(ToolNotAllowed):
        sb.check_tool("rm_rf")


def test_sandbox_path_traversal():
    sb = Sandbox(allowed_tools=["file_read"])
    with pytest.raises(ToolNotAllowed, match="穿越"):
        sb.check_and_sanitize("file_read", {"path": "../../etc/passwd"})


def test_sandbox_sanitize_adds_boundary():
    sb = Sandbox(allowed_tools=["shell"])
    result = sb.sanitize_args("shell", {"content": "hello world"})
    assert "ORCHESTRA_INPUT_START" in result["content"]
    assert "hello world" in result["content"]
    assert "ORCHESTRA_INPUT_END" in result["content"]


def test_sandbox_allows_normal_path():
    sb = Sandbox(allowed_tools=["file_read"])
    result = sb.check_and_sanitize("file_read", {"path": "/opt/project/main.py"})
    assert result["path"] == "/opt/project/main.py"


# ── MockAdapter ──────────────────────────────

def _make_task() -> TaskInput:
    return TaskInput(
        workflow_id="wf-1",
        stage_name="code",
        agent_name="walnut",
        role="developer",
        tools=[],
        input={"task": "test"},
        idempotency_key="wf-1/code",
    )


@pytest.mark.asyncio
async def test_mock_success():
    adapter = MockAgentAdapter("walnut", output={"patch": "ok"})
    result = await adapter.execute_task(_make_task())
    assert result.output == {"patch": "ok"}
    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_mock_fail():
    adapter = MockAgentAdapter("walnut", behavior=MockBehavior.FAIL, error_message="boom")
    with pytest.raises(RuntimeError, match="boom"):
        await adapter.execute_task(_make_task())


@pytest.mark.asyncio
async def test_mock_heartbeat_callback():
    heartbeats = []
    adapter = MockAgentAdapter("walnut", output={"result": "ok"})
    await adapter.execute_task(_make_task(), on_heartbeat=heartbeats.append)
    assert len(heartbeats) >= 2  # started + completing


@pytest.mark.asyncio
async def test_mock_health_ready():
    adapter = MockAgentAdapter("walnut")
    h = await adapter.check_health()
    from orchestra.domain.enums import HealthStatus
    assert h.status == HealthStatus.READY


@pytest.mark.asyncio
async def test_mock_capabilities():
    adapter = MockAgentAdapter("walnut", capabilities=["python", "godot"])
    caps = await adapter.get_capabilities()
    assert "python" in caps.capabilities
    assert "godot" in caps.capabilities


@pytest.mark.asyncio
async def test_mock_cancel():
    adapter = MockAgentAdapter("walnut")
    # cancel 不抛出异常，直接完成
    await adapter.cancel_task("task-1", timedelta(seconds=5))


# ── Registry 辅助函数 ──

def test_list_adapter_names():
    """list_adapter_names 返回已注册的所有 adapter。"""
    from orchestra.adapters.registry import build_registry, list_adapter_names
    from orchestra.domain.agent import Profile, Role
    from orchestra.domain.enums import HealthStatus

    profiles = {
        "walnut": Profile(
            name="walnut", role=Role.DEVELOPER,
            capabilities=["python", "godot"],
            mcpEndpoint="mcp://localhost:18761",
        ),
        "almond": Profile(
            name="almond", role=Role.TESTER,
            capabilities=["pytest"],
            mcpEndpoint="mcp://localhost:18762",
        ),
    }
    build_registry(profiles, use_mock=True)
    names = list_adapter_names()
    assert set(names) == {"walnut", "almond"}


def test_get_profile_task_queue():
    """get_profile_task_queue 返回 'agent-{name}' 格式。"""
    from orchestra.adapters.registry import get_profile_task_queue
    assert get_profile_task_queue("walnut") == "agent-walnut"
    assert get_profile_task_queue("grape") == "agent-grape"
