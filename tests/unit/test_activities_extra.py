"""单元测试：activities 补充分支。

覆盖 compensation / artifact / audit / notification 的成功、幂等、错误分支，
以及 agent_resolver / agent_task 的缓存、取消、Schema 校验与结构化输出提取。

不依赖 Temporal Server：activity context 用 unittest.mock 替代；
幂等 / Artifact / 审计存储使用 memory 后端或 tmp_path。

参见 CLAUDE.md §4 幂等铁律、docs/design.md "Saga 补偿"。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.adapters.mock import MockAgentAdapter, MockBehavior
from orchestra.domain.enums import Role
from orchestra.domain.errors import SchemaViolation
from orchestra.domain.state import TaskInput

# ──────────────────────────────────────────────
# 通用替身
# ──────────────────────────────────────────────


class _FakeActivityInfo:
    def __init__(
        self,
        workflow_id: str = "wf-test",
        activity_id: str = "act-1",
        attempt: int = 1,
        heartbeat_details: list | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.activity_id = activity_id
        self.attempt = attempt
        self.heartbeat_details = heartbeat_details if heartbeat_details is not None else []


def _activity_ctx(info: _FakeActivityInfo | None = None, *, cancelled: bool = False) -> MagicMock:
    mock = MagicMock()
    mock.info.return_value = info or _FakeActivityInfo()
    mock.heartbeat = MagicMock()
    mock.is_cancelled = MagicMock(return_value=cancelled)
    return mock


def _register(name: str, **kwargs) -> MockAgentAdapter:
    import orchestra.adapters.registry as reg

    adapter = MockAgentAdapter(name, **kwargs)
    reg._REGISTRY[name] = adapter
    return adapter


def _put(adapter: MockAgentAdapter) -> MockAgentAdapter:
    import orchestra.adapters.registry as reg

    reg._REGISTRY[adapter.profile_name] = adapter
    return adapter


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _fake_httpx_client(*, status_code: int = 200, error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if error is not None:
        client.post = AsyncMock(side_effect=error)
    else:
        client.post = AsyncMock(return_value=_FakeResponse(status_code))
    return client


def _task(input_data: object | None = None) -> TaskInput:
    return TaskInput(
        workflow_id="wf-test",
        stage_name="code",
        agent_name="walnut",
        role="developer",
        tools=[],
        input=input_data if input_data is not None else {"task": "x"},
        idempotency_key="wf-test/act-1",
    )


# ──────────────────────────────────────────────
# compensation
# ──────────────────────────────────────────────


async def test_run_compensation_success_then_cached():
    from orchestra.activities.compensation import CompensationInput, run_compensation
    from orchestra.state.idempotency import init_store

    init_store("memory")
    adapter = _register("walnut", output={"reverted": True})
    mock_act = _activity_ctx()
    inp = CompensationInput(
        for_stage="code", agent_name="walnut", action="revert", input_data={"n": 1}
    )

    with patch("orchestra.activities.compensation.activity", mock_act):
        first = await run_compensation(inp)
        second = await run_compensation(inp)

    assert first == {"success": True, "output": {"reverted": True}}
    assert second == first
    assert adapter.call_count == 1
    assert mock_act.heartbeat.call_count >= 2
    assert adapter.last_task is not None
    assert adapter.last_task.stage_name == "compensation_code"


async def test_run_compensation_failure_reraises():
    from orchestra.activities.compensation import CompensationInput, run_compensation
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("almond", behavior=MockBehavior.FAIL, error_message="compensation boom")
    mock_act = _activity_ctx()
    inp = CompensationInput(
        for_stage="deploy", agent_name="almond", action="rollback", input_data={}
    )

    with patch("orchestra.activities.compensation.activity", mock_act):
        with pytest.raises(RuntimeError, match="compensation boom"):
            await run_compensation(inp)


# ──────────────────────────────────────────────
# artifact
# ──────────────────────────────────────────────


async def test_put_artifact_success_then_cached(tmp_path):
    from orchestra.activities.artifact import ArtifactPutInput, put_artifact
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.state.idempotency import init_store

    init_store("memory")
    init_artifact_store(tmp_path / "artifacts")
    src = tmp_path / "result.txt"
    src.write_text("hello artifact", encoding="utf-8")
    inp = ArtifactPutInput(
        source_path=str(src),
        namespace="ns",
        pipeline_name="pipeline",
        run_id="run-1",
        stage_name="build",
        artifact_name="output",
    )
    mock_act = _activity_ctx()

    with patch("orchestra.activities.artifact.activity", mock_act):
        ref = await put_artifact(inp)
        cached = await put_artifact(inp)

    assert ref.storage == "local"
    assert Path(ref.path).read_text(encoding="utf-8") == "hello artifact"
    assert cached == ref
    assert mock_act.heartbeat.call_count >= 2


async def test_put_artifact_with_retention_and_compress(tmp_path):
    from orchestra.activities.artifact import ArtifactPutInput, put_artifact
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.state.idempotency import init_store

    init_store("memory")
    init_artifact_store(tmp_path / "artifacts")
    src = tmp_path / "bundle.txt"
    src.write_text("payload", encoding="utf-8")
    inp = ArtifactPutInput(
        source_path=str(src),
        namespace="ns",
        pipeline_name="pipeline",
        run_id="run-2",
        stage_name="package",
        artifact_name="bundle",
        compress=True,
        retention_days=3,
    )

    with patch("orchestra.activities.artifact.activity", _activity_ctx()):
        ref = await put_artifact(inp)

    assert ref.size > 0
    assert Path(ref.path).name.endswith(".gz")


# ──────────────────────────────────────────────
# audit
# ──────────────────────────────────────────────


async def test_write_audit_log_persists_event(tmp_path, monkeypatch):
    import orchestra.activities.audit as audit_activity
    from orchestra.observability import audit as audit_obs

    db = tmp_path / "audits.db"
    monkeypatch.setattr(audit_obs, "_default_writer", audit_obs.AuditWriter(db))
    inp = audit_activity.AuditInput(
        actor="alice",
        action="approval.approve",
        resource="pipeline/p1",
        result="ok",
        version="v1",
        ip_address="1.2.3.4",
        user_agent="pytest",
        diff={"before": "pending"},
        extra={"note": "x"},
    )

    with patch.object(audit_activity, "activity", _activity_ctx()):
        assert await audit_activity.write_audit_log(inp) is None

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT actor, action, resource, result, diff_json, extra_json FROM audits"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "alice"
    assert row[1] == "approval.approve"
    assert row[3] == "ok"
    assert row[4] == '{"before": "pending"}'
    assert row[5] == '{"note": "x"}'


async def test_write_audit_log_swallows_writer_failure():
    import orchestra.activities.audit as audit_activity

    inp = audit_activity.AuditInput(
        actor="bob", action="pipeline.submit", resource="pipeline/p2", result="ok"
    )

    with patch.object(audit_activity, "activity", _activity_ctx()):
        with patch.object(audit_activity, "get_writer", side_effect=RuntimeError("db down")):
            assert await audit_activity.write_audit_log(inp) is None


# ──────────────────────────────────────────────
# notification
# ──────────────────────────────────────────────


def _notification(channel: str, event: str = "succeeded"):
    from orchestra.activities.notification import NotificationInput

    return NotificationInput(
        channel=channel, target="t", message="done", pipeline_id="p1", event=event
    )


async def test_send_notification_feishu_success(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.example/hook")
    client = _fake_httpx_client(status_code=200)

    with patch.object(notification, "activity", _activity_ctx()):
        with patch.object(notification.httpx, "AsyncClient", return_value=client):
            result = await notification.send_notification(_notification("feishu"))

    assert result == {"sent": True, "status_code": 200}
    payload = client.post.call_args.kwargs["json"]
    assert payload["msg_type"] == "interactive"
    assert "[SUCCEEDED] p1" in payload["card"]["header"]["title"]["content"]


async def test_send_notification_feishu_non_200(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.example/hook")
    client = _fake_httpx_client(status_code=500)

    with patch.object(notification, "activity", _activity_ctx()):
        with patch.object(notification.httpx, "AsyncClient", return_value=client):
            result = await notification.send_notification(_notification("feishu"))

    assert result == {"sent": False, "status_code": 500}


async def test_send_notification_feishu_not_configured(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)

    with patch.object(notification, "activity", _activity_ctx()):
        result = await notification.send_notification(_notification("feishu"))

    assert result == {"sent": False, "reason": "FEISHU_WEBHOOK_URL not set"}


async def test_send_notification_feishu_transport_error(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.example/hook")
    client = _fake_httpx_client(error=RuntimeError("network down"))

    with patch.object(notification, "activity", _activity_ctx()):
        with patch.object(notification.httpx, "AsyncClient", return_value=client):
            result = await notification.send_notification(_notification("feishu"))

    assert result == {"sent": False, "reason": "network down"}


async def test_send_notification_slack_success(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://slack.example/hook")
    client = _fake_httpx_client(status_code=200)

    with patch.object(notification, "activity", _activity_ctx()):
        with patch.object(notification.httpx, "AsyncClient", return_value=client):
            result = await notification.send_notification(_notification("slack"))

    assert result == {"sent": True}


async def test_send_notification_slack_not_configured_and_error(monkeypatch):
    from orchestra.activities import notification

    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with patch.object(notification, "activity", _activity_ctx()):
        unset = await notification.send_notification(_notification("slack"))
    assert unset == {"sent": False, "reason": "SLACK_WEBHOOK_URL not set"}

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://slack.example/hook")
    client = _fake_httpx_client(error=RuntimeError("slack down"))
    with patch.object(notification, "activity", _activity_ctx()):
        with patch.object(notification.httpx, "AsyncClient", return_value=client):
            failed = await notification.send_notification(_notification("slack"))
    assert failed == {"sent": False, "reason": "slack down"}


async def test_send_notification_email_and_unknown_channel():
    from orchestra.activities import notification

    with patch.object(notification, "activity", _activity_ctx()):
        email = await notification.send_notification(_notification("email"))
        unknown = await notification.send_notification(_notification("pager"))

    assert email == {"sent": False, "reason": "email not implemented"}
    assert unknown == {"sent": False, "reason": "unknown channel: pager"}


# ──────────────────────────────────────────────
# agent_resolver
# ──────────────────────────────────────────────


async def test_resolve_agent_by_selector_success_then_cached():
    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    _register("almond", role=Role.TESTER, capabilities=["pytest"])
    info = _FakeActivityInfo(workflow_id="wf-resolve", activity_id="a1")
    inp = AgentResolveInput(role="developer", capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx(info)):
        first = await resolve_agent_by_selector(inp)
        second = await resolve_agent_by_selector(inp)

    assert first.agent_name == "walnut"
    assert first.task_queue == "agent-walnut"
    assert first.role == "developer"
    assert second == first


async def test_resolve_agent_by_selector_no_match_is_non_retryable():
    from temporalio.exceptions import ApplicationError

    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    inp = AgentResolveInput(role="tester", capabilities=["pytest"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx()):
        with pytest.raises(ApplicationError) as exc:
            await resolve_agent_by_selector(inp)

    assert exc.value.non_retryable is True


async def test_resolve_agent_by_selector_capability_mismatch_is_non_retryable():
    from temporalio.exceptions import ApplicationError

    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    # role 匹配但 capability 不满足
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    inp = AgentResolveInput(role="developer", capabilities=["godot"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx()):
        with pytest.raises(ApplicationError) as exc:
            await resolve_agent_by_selector(inp)

    assert exc.value.non_retryable is True


async def test_resolve_agent_by_selector_cancelled_before_scan():
    from temporalio.exceptions import CancelledError

    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx(cancelled=True)):
        with pytest.raises(CancelledError):
            await resolve_agent_by_selector(AgentResolveInput(role="developer"))


async def test_resolve_agent_by_selector_cancelled_mid_scan():
    from temporalio.exceptions import CancelledError

    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    mock_act = _activity_ctx()
    mock_act.is_cancelled.side_effect = [False, True]

    with patch("orchestra.activities.agent_resolver.activity", mock_act):
        with pytest.raises(CancelledError):
            await resolve_agent_by_selector(AgentResolveInput(role="developer"))


async def test_resolve_agent_by_selector_skips_timeout_and_error():
    from orchestra.activities.agent_resolver import AgentResolveInput, resolve_agent_by_selector
    from orchestra.state.idempotency import init_store

    init_store("memory")
    slow = _put(MockAgentAdapter("slow", role=Role.DEVELOPER, capabilities=["python"]))
    slow.get_capabilities = AsyncMock(side_effect=TimeoutError())
    broken = _put(MockAgentAdapter("broken", role=Role.DEVELOPER, capabilities=["python"]))
    broken.get_capabilities = AsyncMock(side_effect=RuntimeError("mcp down"))
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx()):
        result = await resolve_agent_by_selector(AgentResolveInput(role="developer"))

    assert result.agent_name == "walnut"


async def test_resolve_all_matching_agents_success_then_cached():
    from orchestra.activities.agent_resolver import (
        AgentResolveInput,
        resolve_all_matching_agents,
    )
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    _register("chestnut", role=Role.DEVELOPER, capabilities=["python", "godot"])
    info = _FakeActivityInfo(workflow_id="wf-resolve-all", activity_id="a1")
    inp = AgentResolveInput(role="developer", capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx(info)):
        first = await resolve_all_matching_agents(inp)
        second = await resolve_all_matching_agents(inp)

    assert {m.agent_name for m in first.matches} == {"walnut", "chestnut"}
    assert {m.agent_name for m in second.matches} == {"walnut", "chestnut"}


async def test_resolve_all_matching_agents_cancelled_before_and_mid_loop():
    from temporalio.exceptions import CancelledError

    from orchestra.activities.agent_resolver import (
        AgentResolveInput,
        resolve_all_matching_agents,
    )
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])
    inp = AgentResolveInput(role="developer")

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx(cancelled=True)):
        with pytest.raises(CancelledError):
            await resolve_all_matching_agents(inp)

    mock_act = _activity_ctx()
    mock_act.is_cancelled.side_effect = [False, True]
    with patch("orchestra.activities.agent_resolver.activity", mock_act):
        # 回归：循环内第二次 is_cancelled 命中时必须抛 CancelledError。
        # （曾因 CancelledError 只在首个 if 内 import 而抛 UnboundLocalError，
        #   现已把 import 提到模块顶层）
        with pytest.raises(CancelledError):
            await resolve_all_matching_agents(inp)


async def test_resolve_all_matching_agents_no_match_is_non_retryable():
    from temporalio.exceptions import ApplicationError

    from orchestra.activities.agent_resolver import (
        AgentResolveInput,
        resolve_all_matching_agents,
    )
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx()):
        with pytest.raises(ApplicationError) as exc:
            await resolve_all_matching_agents(AgentResolveInput(role="tester"))

    assert exc.value.non_retryable is True


async def test_resolve_all_skips_broken_adapter():
    from orchestra.activities.agent_resolver import (
        AgentResolveInput,
        resolve_all_matching_agents,
    )
    from orchestra.state.idempotency import init_store

    init_store("memory")
    broken = _put(MockAgentAdapter("broken", role=Role.DEVELOPER, capabilities=["python"]))
    broken.get_capabilities = AsyncMock(side_effect=RuntimeError("mcp down"))
    _register("walnut", role=Role.DEVELOPER, capabilities=["python"])

    with patch("orchestra.activities.agent_resolver.activity", _activity_ctx()):
        result = await resolve_all_matching_agents(AgentResolveInput(role="developer"))

    assert [m.agent_name for m in result.matches] == ["walnut"]


# ──────────────────────────────────────────────
# agent_task：结构化输出提取
# ──────────────────────────────────────────────


def test_extract_structured_output_variants():
    from orchestra.activities.agent_task import _extract_structured_output

    assert _extract_structured_output("raw", "s") == "raw"
    assert _extract_structured_output({"plain": 1}, "s") == {"plain": 1}
    assert _extract_structured_output(
        {"tool_calls": [{"name": "submit_result", "arguments": '{"patch": "p"}'}]}, "s"
    ) == {"patch": "p"}
    assert _extract_structured_output(
        {"tool_use": [{"function": {"name": "submit_result", "arguments": {"a": 1}}}]}, "s"
    ) == {"a": 1}
    assert _extract_structured_output({"name": "submit_result", "arguments": '{"c": 3}'}, "s") == {
        "c": 3
    }
    assert _extract_structured_output(
        {"output": {"name": "submit_result", "parameters": {"d": 4}}}, "s"
    ) == {"d": 4}


def test_extract_structured_output_falls_back_to_raw():
    from orchestra.activities.agent_task import _extract_structured_output

    # tool_calls 非 list → 不解析
    assert _extract_structured_output({"tool_calls": "nope"}, "s") == {"tool_calls": "nope"}
    # submit_result 但 arguments 是坏 JSON → 保持原样
    assert _extract_structured_output(
        {"result": {"name": "submit_result", "arguments": "{bad json"}}, "s"
    ) == {"result": {"name": "submit_result", "arguments": "{bad json"}}


# ──────────────────────────────────────────────
# agent_task：缓存 / Schema / checkpoint
# ──────────────────────────────────────────────


async def test_execute_agent_task_stage_cache_hit():
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("memory")
    adapter = _register("walnut", output={"patch": "cached"})
    mock_act = _activity_ctx()
    mock_act.info.side_effect = [
        _FakeActivityInfo(activity_id="a1"),
        _FakeActivityInfo(activity_id="a2"),
    ]
    inp = AgentTaskInput(task=_task(), stage_name="code", pipeline_name="p", cache_enabled=True)

    with patch("orchestra.activities.agent_task.activity", mock_act):
        first = await execute_agent_task(inp)
        second = await execute_agent_task(inp)

    assert first.output_value == {"patch": "cached"}
    assert second.output_value == {"patch": "cached"}
    assert adapter.call_count == 1


async def test_execute_agent_task_input_schema_violation_fails():
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", output={"ok": True})
    inp = AgentTaskInput(
        task=_task({"task": "x"}),
        stage_name="code",
        pipeline_name="p",
        input_schema={"type": "object", "required": ["missing"]},
    )

    with patch("orchestra.activities.agent_task.activity", _activity_ctx()):
        with pytest.raises(SchemaViolation):
            await execute_agent_task(inp)


async def test_execute_agent_task_input_schema_warn_policy():
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", output={"ok": True})
    inp = AgentTaskInput(
        task=_task({"task": "x"}),
        stage_name="code",
        pipeline_name="p",
        input_schema={"type": "object", "required": ["missing"]},
        schema_violation_policy="warn",
    )

    with patch("orchestra.activities.agent_task.activity", _activity_ctx()):
        result = await execute_agent_task(inp)

    assert result.success is True


async def test_execute_agent_task_output_schema_violation_fails():
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", output={})
    inp = AgentTaskInput(
        task=_task(),
        stage_name="code",
        pipeline_name="p",
        output_schema={"type": "object", "required": ["needed"]},
    )

    with patch("orchestra.activities.agent_task.activity", _activity_ctx()):
        with pytest.raises(SchemaViolation):
            await execute_agent_task(inp)


async def test_execute_agent_task_checkpoint_resume_and_bad_checkpoint():
    from orchestra.activities.agent_task import AgentTaskInput, execute_agent_task
    from orchestra.state.idempotency import init_store

    init_store("memory")
    _register("walnut", output={"ok": True})
    inp = AgentTaskInput(task=_task(), stage_name="code", pipeline_name="p")

    good = _FakeActivityInfo(
        activity_id="cp-good",
        heartbeat_details=[{"checkpoint": {"step": "compile", "progress": 40, "data": {"x": 1}}}],
    )
    with patch("orchestra.activities.agent_task.activity", _activity_ctx(good)):
        first = await execute_agent_task(inp)
    assert first.success is True

    bad = _FakeActivityInfo(activity_id="cp-bad", heartbeat_details=[{"checkpoint": 123}])
    with patch("orchestra.activities.agent_task.activity", _activity_ctx(bad)):
        second = await execute_agent_task(inp)
    assert second.success is True
