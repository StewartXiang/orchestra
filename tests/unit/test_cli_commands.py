"""单元测试：CLI 各子命令（CliRunner + monkeypatch，不连真实 Temporal / 网络）。

覆盖 ``src/orchestra/cli/commands/*.py`` 与 ``src/orchestra/cli/client.py``：
用 ``monkeypatch`` 替换 ``orchestra.worker.registry.make_client`` /
``orchestra.cli.client.get_client`` / ``orchestra.adapters.mcp.MCPAdapter`` 等注入点，
命令内部一律不发起真实连接。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.cli.commands.schedule import _parse_cron
from orchestra.cli.main import main
from orchestra.domain.enums import HealthStatus, Role

ROOT = Path(__file__).parent.parent.parent
MINIMAL = str(ROOT / "examples" / "minimal.pipeline.yaml")


@pytest.fixture
def runner():
    return CliRunner()


# ────────────────────────── helpers ──────────────────────────


async def _aiter(items):
    """模拟 Temporal SDK 的 async iterator（如 list_workflows / list_schedules）。"""
    for item in items:
        yield item


def _patch_make_client(monkeypatch, client):
    monkeypatch.setattr("orchestra.worker.registry.make_client", AsyncMock(return_value=client))


def _patch_cli_client(monkeypatch, client):
    monkeypatch.setattr("orchestra.cli.client.get_client", AsyncMock(return_value=client))


def _workflow_client(handle):
    client = MagicMock()
    client.namespace = "default"
    client.get_workflow_handle = MagicMock(return_value=handle)
    return client


def _desc(**overrides):
    base = {
        "id": "wf-1",
        "run_id": "run-1",
        "status": "RUNNING",
        "start_time": None,
        "close_time": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ────────────────────────── status ──────────────────────────


class _StatusHandle:
    def __init__(self, *, approval_error=False, dag_error=False, query_error=False):
        self.approval_error = approval_error
        self.dag_error = dag_error
        self.query_error = query_error

    async def describe(self):
        return _desc()

    async def query(self, name, *args):
        if name == "get_approval_status":
            if self.approval_error:
                raise RuntimeError("no approvals")
            return "PENDING"
        if name == "get_dag_status":
            if self.dag_error:
                raise RuntimeError("no dag")
            return SimpleNamespace(completed=["code"], running=["test"])
        if self.query_error:
            raise RuntimeError("query failed")
        return {"echo": name}


def _wf(wf_id, run_id="0123456789abcdef", status="WORKFLOW_EXECUTION_STATUS_RUNNING"):
    return SimpleNamespace(
        id=wf_id,
        run_id=run_id,
        status=status,
        start_time="2024-01-01T00:00:00.123456Z",
    )


def test_status_detail_all_fields(runner, monkeypatch):
    _patch_make_client(monkeypatch, _workflow_client(_StatusHandle()))
    result = runner.invoke(
        main,
        ["-o", "json", "status", "wf-1", "--pending-approvals", "--query", "my_query"],
    )
    assert result.exit_code == 0, result.output
    assert "PENDING" in result.output
    assert "stages_completed" in result.output
    assert "query_result" in result.output


def test_status_detail_error_branches(runner, monkeypatch):
    handle = _StatusHandle(approval_error=True, dag_error=True, query_error=True)
    _patch_make_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["status", "wf-1", "--pending-approvals", "--query", "q"])
    assert result.exit_code == 0, result.output
    assert "(不适用)" in result.output
    assert "query_error" in result.output
    assert "wf-1" in result.output


def test_status_list_rows(runner, monkeypatch):
    client = MagicMock()
    client.list_workflows = MagicMock(return_value=_aiter([_wf("wf-a"), _wf("wf-b")]))
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["-o", "json", "status"])
    assert result.exit_code == 0, result.output
    assert "wf-a" in result.output
    assert "RUNNING" in result.output


def test_status_list_empty(runner, monkeypatch):
    client = MagicMock()
    client.list_workflows = MagicMock(return_value=_aiter([]))
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["-o", "yaml", "status"])
    assert result.exit_code == 0, result.output
    assert "无运行中的流水线" in result.output


def test_status_list_error(runner, monkeypatch):
    client = MagicMock()
    client.list_workflows = MagicMock(side_effect=RuntimeError("visibility down"))
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    assert "查询失败" in result.output


def test_status_connect_failure(runner, monkeypatch):
    monkeypatch.setattr(
        "orchestra.worker.registry.make_client", AsyncMock(side_effect=RuntimeError("refused"))
    )
    result = runner.invoke(main, ["status", "wf-1"])
    assert result.exit_code == 0, result.output
    assert "无法连接 Temporal" in result.output


def test_status_watch_stops_on_interrupt(runner, monkeypatch):
    _patch_make_client(monkeypatch, _workflow_client(_StatusHandle()))

    async def _boom(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("orchestra.cli.commands.status.asyncio.sleep", _boom)
    result = runner.invoke(main, ["status", "wf-1", "--watch"])
    assert result.exit_code == 0, result.output
    assert "wf-1" in result.output


# ────────────────────────── logs / inspect ──────────────────────────


def test_logs_success(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=SimpleNamespace(id="wf-1", status="RUNNING"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["logs", "wf-1", "--stage", "code", "--follow"])
    assert result.exit_code == 0, result.output
    assert "Pipeline: wf-1" in result.output
    assert "Filtering for stage: code" in result.output


def test_logs_connect_failure(runner, monkeypatch):
    monkeypatch.setattr(
        "orchestra.cli.client.get_client", AsyncMock(side_effect=RuntimeError("down"))
    )
    result = runner.invoke(main, ["logs", "wf-1"])
    assert result.exit_code != 0


def test_inspect_stdout(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=_desc())
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["inspect", "wf-1"])
    assert result.exit_code == 0, result.output
    assert '"id": "wf-1"' in result.output


def test_inspect_download_history(runner, monkeypatch, tmp_path):
    out_file = tmp_path / "history.json"
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=_desc())
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["inspect", "wf-1", "--download-history", str(out_file)])
    assert result.exit_code == 0, result.output
    assert "history saved" in result.output
    assert json.loads(out_file.read_text())["id"] == "wf-1"


def test_inspect_describe_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RuntimeError("gone"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["inspect", "wf-1"])
    assert result.exit_code != 0


# ────────────────────────── cancel / signal ──────────────────────────


def test_cancel_confirmed(runner, monkeypatch):
    handle = MagicMock()
    handle.cancel = AsyncMock()
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["cancel", "wf-1"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "cancel signal sent" in result.output
    handle.cancel.assert_awaited_once()


def test_cancel_force(runner, monkeypatch):
    handle = MagicMock()
    handle.terminate = AsyncMock()
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["cancel", "wf-1", "--force", "-y"])
    assert result.exit_code == 0, result.output
    assert "terminated" in result.output
    handle.terminate.assert_awaited_once()


def test_cancel_aborted(runner, monkeypatch):
    _patch_cli_client(monkeypatch, _workflow_client(MagicMock()))
    result = runner.invoke(main, ["cancel", "wf-1"], input="n\n")
    assert result.exit_code != 0


def test_cancel_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.cancel = AsyncMock(side_effect=RuntimeError("boom"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["cancel", "wf-1", "-y"])
    assert result.exit_code != 0


def test_signal_success(runner, monkeypatch):
    handle = MagicMock()
    handle.signal = AsyncMock()
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["signal", "wf-1", "pause", "--data", '{"reason": "maint"}'])
    assert result.exit_code == 0, result.output
    assert "signal 'pause' sent to wf-1" in result.output
    handle.signal.assert_awaited_once_with("pause", {"reason": "maint"})


def test_signal_bad_json(runner, monkeypatch):
    handle = MagicMock()
    handle.signal = AsyncMock()
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["signal", "wf-1", "pause", "--data", "{bad"])
    assert result.exit_code != 0
    handle.signal.assert_not_awaited()


# ────────────────────────── approve / reject ──────────────────────────


def test_approve_success(runner, monkeypatch):
    handle = MagicMock()
    handle.execute_update = AsyncMock(return_value=SimpleNamespace(approved_at="2024-01-01T00:00"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["approve", "wf-1", "code", "--as", "alice", "--reason", "lgtm"])
    assert result.exit_code == 0, result.output
    assert "approved stage 'code'" in result.output
    update = handle.execute_update.await_args.args[1]
    assert update.stage_name == "code" and update.approver == "alice"


def test_approve_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.execute_update = AsyncMock(side_effect=RuntimeError("not waiting"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["approve", "wf-1", "code"])
    assert result.exit_code != 0


def test_reject_success(runner, monkeypatch):
    handle = MagicMock()
    handle.execute_update = AsyncMock()
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["reject", "wf-1", "code", "--reason", "bad output"])
    assert result.exit_code == 0, result.output
    assert "rejected stage 'code'" in result.output
    update = handle.execute_update.await_args.args[1]
    assert update.reason == "bad output"


def test_reject_requires_reason(runner, monkeypatch):
    _patch_cli_client(monkeypatch, _workflow_client(MagicMock()))
    result = runner.invoke(main, ["reject", "wf-1", "code"])
    assert result.exit_code != 0


# ────────────────────────── list / client ──────────────────────────


def test_list_pipelines_success(runner, monkeypatch):
    _patch_cli_client(monkeypatch, MagicMock())
    result = runner.invoke(
        main, ["list", "--status", "Running", "--since", "1h", "--limit", "5", "--label", "a=b"]
    )
    assert result.exit_code == 0, result.output
    assert "[list] since=1h status=Running limit=5" in result.output


def test_list_pipelines_failure(runner, monkeypatch):
    monkeypatch.setattr(
        "orchestra.cli.client.get_client", AsyncMock(side_effect=RuntimeError("down"))
    )
    result = runner.invoke(main, ["list"])
    assert result.exit_code != 0


def test_cli_client_get_client(monkeypatch):
    from orchestra.cli.client import get_client

    fake_client = MagicMock()
    fake_make = AsyncMock(return_value=fake_client)
    monkeypatch.setattr("orchestra.worker.registry.make_client", fake_make)
    assert asyncio.run(get_client("h:7233", "ns")) is fake_client
    fake_make.assert_awaited_once_with("h:7233", "ns")


# ────────────────────────── re-run ──────────────────────────


def _task_completed(event_id):
    event = MagicMock()
    event.event_id = event_id
    event.HasField = lambda name: name == "workflow_task_completed"
    return event


def _activity_scheduled(event_id, stage_name, activity_name="execute_agent_task"):
    payload = json.dumps({"stage_name": stage_name}).encode()
    event = MagicMock()
    event.event_id = event_id
    event.HasField = lambda name: name == "activity_task_scheduled"
    event.activity_task_scheduled_event_attributes = SimpleNamespace(
        activity_type=SimpleNamespace(name=activity_name),
        input=SimpleNamespace(payloads=[SimpleNamespace(data=payload)]),
    )
    return event


def _activity_scheduled_bad_payload(event_id):
    event = MagicMock()
    event.event_id = event_id
    event.HasField = lambda name: name == "activity_task_scheduled"
    event.activity_task_scheduled_event_attributes = SimpleNamespace(
        activity_type=SimpleNamespace(name="execute_agent_task"),
        input=SimpleNamespace(payloads=[SimpleNamespace(data=b"not-json")]),
    )
    return event


def _rerun_events():
    return [
        _activity_scheduled(1, "code", activity_name="other_activity"),
        _activity_scheduled_bad_payload(2),
        _activity_scheduled(10, "code"),
        _task_completed(15),
        _task_completed(20),
    ]


def _rerun_client(events):
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=SimpleNamespace(status="RUNNING", run_id="run-1"))
    handle.fetch_history = AsyncMock(return_value=SimpleNamespace(events=events))
    client = _workflow_client(handle)
    client.workflow_service = MagicMock()
    client.workflow_service.reset_workflow_execution = AsyncMock()
    return client


def test_re_run_from_stage(runner, monkeypatch):
    client = _rerun_client(_rerun_events())
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["re-run", "wf-1", "--from-stage", "code", "--yes"])
    assert result.exit_code == 0, result.output
    assert "workflow 已重置" in result.output
    request = client.workflow_service.reset_workflow_execution.await_args.args[0]
    assert request.workflow_task_finish_event_id == 15


def test_re_run_stage_not_found(runner, monkeypatch):
    client = _rerun_client(_rerun_events())
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["re-run", "wf-1", "--from-stage", "nope", "--yes"])
    assert result.exit_code == 0, result.output
    assert "未找到 stage" in result.output
    request = client.workflow_service.reset_workflow_execution.await_args.args[0]
    assert request.workflow_task_finish_event_id == 15


def test_re_run_from_start(runner, monkeypatch):
    client = _rerun_client(_rerun_events())
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["re-run", "wf-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "from start" in result.output


def test_re_run_no_reset_event(runner, monkeypatch):
    _patch_cli_client(monkeypatch, _rerun_client([_activity_scheduled(1, "code")]))
    result = runner.invoke(main, ["re-run", "wf-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "找不到可重置" in result.output


def test_re_run_cancelled_by_confirm(runner, monkeypatch):
    _patch_cli_client(monkeypatch, _rerun_client(_rerun_events()))
    result = runner.invoke(main, ["re-run", "wf-1"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "已取消" in result.output


def test_re_run_describe_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RuntimeError("not found"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["re-run", "wf-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "找不到 workflow" in result.output


def test_re_run_fetch_history_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=SimpleNamespace(status="RUNNING", run_id="r"))
    handle.fetch_history = AsyncMock(side_effect=RuntimeError("history gone"))
    _patch_cli_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["re-run", "wf-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "重跑失败" in result.output


# ────────────────────────── replay ──────────────────────────


def test_replay_no_history_file(runner):
    result = runner.invoke(main, ["replay", "wf-1"])
    assert result.exit_code == 0, result.output
    assert "[replay]" in result.output


def _patch_replayer(monkeypatch, replay_failure=None):
    fake_history = MagicMock()
    fake_history.from_json.return_value = object()
    monkeypatch.setattr("temporalio.client.WorkflowHistory", fake_history)
    replayer = MagicMock()
    replayer.replay_workflow = AsyncMock(
        return_value=SimpleNamespace(replay_failure=replay_failure)
    )
    monkeypatch.setattr("temporalio.worker.Replayer", MagicMock(return_value=replayer))
    monkeypatch.setattr(
        "orchestra.worker.registry.build_workflow_runner", MagicMock(return_value=object())
    )


def test_replay_history_passed(runner, monkeypatch, tmp_path):
    history_file = tmp_path / "hist.json"
    history_file.write_text("{}")
    _patch_replayer(monkeypatch)
    result = runner.invoke(main, ["replay", "wf-1", "--history-file", str(history_file)])
    assert result.exit_code == 0, result.output
    assert "Replay passed" in result.output


def test_replay_history_failure(runner, monkeypatch, tmp_path):
    history_file = tmp_path / "hist.json"
    history_file.write_text("{}")
    _patch_replayer(monkeypatch, replay_failure=RuntimeError("nondeterminism"))
    result = runner.invoke(main, ["replay", "wf-1", "--history-file", str(history_file)])
    assert result.exit_code != 0


# ────────────────────────── schedule ──────────────────────────


def test_parse_cron_all_field_kinds():
    spec = _parse_cron("1,2 3 */2 4 5")
    assert spec is not None
    assert [r.start for r in spec.minute] == [1, 2]
    assert [r.start for r in spec.hour] == [3]
    assert spec.day_of_month[0].step == 2
    assert [r.start for r in spec.month] == [4]
    assert [r.start for r in spec.day_of_week] == [5]
    assert _parse_cron("* * * * *") is not None
    assert _parse_cron("bad") is None


def test_schedule_create(runner, monkeypatch):
    client = MagicMock()
    client.create_schedule = AsyncMock()
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(
        main, ["schedule", "create", MINIMAL, "--cron", "0 9 * * *", "--param", "task=hi"]
    )
    assert result.exit_code == 0, result.output
    assert "✓ schedule created" in result.output
    client.create_schedule.assert_awaited_once()


def test_schedule_create_invalid_cron(runner, monkeypatch):
    client = MagicMock()
    client.create_schedule = AsyncMock()
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["schedule", "create", MINIMAL, "--cron", "bad"])
    assert result.exit_code == 0, result.output
    assert "无效 cron" in result.output
    client.create_schedule.assert_not_awaited()


def test_schedule_list(runner, monkeypatch):
    client = MagicMock()
    client.list_schedules = MagicMock(
        return_value=_aiter(
            [
                SimpleNamespace(
                    id="sched-1", schedule=SimpleNamespace(state=SimpleNamespace(note="note-1"))
                )
            ]
        )
    )
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["schedule", "list"])
    assert result.exit_code == 0, result.output
    assert "sched-1" in result.output
    assert "note-1" in result.output


@pytest.mark.parametrize(
    ("sub", "method", "expected"),
    [
        ("pause", "pause", "schedule paused"),
        ("resume", "resume", "schedule resumed"),
        ("trigger", "trigger", "schedule triggered"),
        ("delete", "delete", "schedule deleted"),
    ],
)
def test_schedule_lifecycle(runner, monkeypatch, sub, method, expected):
    handle = MagicMock()
    setattr(handle, method, AsyncMock())
    client = MagicMock()
    client.get_schedule_handle = MagicMock(return_value=handle)
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["schedule", sub, "sched-9"])
    assert result.exit_code == 0, result.output
    assert expected in result.output
    getattr(handle, method).assert_awaited_once()


def test_schedule_lifecycle_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.delete = AsyncMock(side_effect=RuntimeError("no such schedule"))
    client = MagicMock()
    client.get_schedule_handle = MagicMock(return_value=handle)
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["schedule", "delete", "sched-9"])
    assert result.exit_code != 0


# ────────────────────────── init ──────────────────────────


def test_init_non_interactive(runner, tmp_path):
    result = runner.invoke(main, ["init", "--non-interactive", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "my-pipeline.pipeline.yaml").exists()
    assert (tmp_path / "values.yaml").exists()
    assert "已生成" in result.output


def test_init_interactive_custom(runner, tmp_path):
    user_input = "cool-pipe\nCool pipeline\ndev-agent test-agent\ncode,test,deploy\n"
    result = runner.invoke(main, ["init", "--out", str(tmp_path)], input=user_input)
    assert result.exit_code == 0, result.output
    data = yaml.safe_load((tmp_path / "cool-pipe.pipeline.yaml").read_text())
    assert set(data["spec"]["agents"]) == {"dev-agent", "test-agent"}
    stages = [s["name"] for s in data["spec"]["pipeline"]["stages"]]
    assert stages == ["code", "test", "deploy"]
    assert data["spec"]["pipeline"]["stages"][1]["dependsOn"] == ["code"]


def test_init_interactive_defaults(runner, tmp_path):
    result = runner.invoke(main, ["init", "--out", str(tmp_path)], input="\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "my-pipeline.pipeline.yaml").exists()


def test_init_interactive_fallbacks(runner, tmp_path):
    result = runner.invoke(main, ["init", "--out", str(tmp_path)], input="edge\n\n \n,\n")
    assert result.exit_code == 0, result.output
    data = yaml.safe_load((tmp_path / "edge.pipeline.yaml").read_text())
    assert list(data["spec"]["agents"]) == ["agent-1"]
    assert [s["name"] for s in data["spec"]["pipeline"]["stages"]] == ["code", "test"]


# ────────────────────────── health ──────────────────────────


_PROFILE = SimpleNamespace(
    role=Role.DEVELOPER,
    capabilities=["python", "git", "shell"],
    mcpEndpoint="mcp://localhost:18961",
    tools=["file_read"],
)


def _patch_profiles(monkeypatch, profiles):
    monkeypatch.setattr(
        "orchestra.adapters.registry.load_profiles_from_yaml", lambda _path: profiles
    )


class _ReadyAdapter:
    def __init__(self, *args, **kwargs):
        pass

    async def check_health(self):
        return SimpleNamespace(status=HealthStatus.READY)

    async def close(self):
        pass


class _BoomAdapter:
    def __init__(self, *args, **kwargs):
        pass

    async def check_health(self):
        raise RuntimeError("boom")

    async def close(self):
        pass


class _TimeoutAdapter:
    def __init__(self, *args, **kwargs):
        pass

    async def check_health(self):
        raise TimeoutError

    async def close(self):
        pass


def test_health_no_mcp(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    _patch_make_client(monkeypatch, MagicMock())
    result = runner.invoke(main, ["-o", "json", "health", "--no-mcp"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "connected" in result.output


def test_health_unknown_agent(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    result = runner.invoke(main, ["health", "--no-mcp", "--agent", "ghost"])
    assert result.exit_code == 0, result.output
    assert "未找到 profile" in result.output


def test_health_temporal_timeout(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    monkeypatch.setattr(
        "orchestra.worker.registry.make_client", AsyncMock(side_effect=TimeoutError)
    )
    result = runner.invoke(main, ["health", "--no-mcp", "--agent", "demo"])
    assert result.exit_code == 0, result.output
    assert "✗ timeout" in result.output


def test_health_temporal_error(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    monkeypatch.setattr(
        "orchestra.worker.registry.make_client", AsyncMock(side_effect=RuntimeError("refused"))
    )
    result = runner.invoke(main, ["health", "--no-mcp", "--agent", "demo"])
    assert result.exit_code == 0, result.output
    assert "✗ refused" in result.output


def test_health_mcp_ready(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    _patch_make_client(monkeypatch, MagicMock())
    monkeypatch.setattr("orchestra.adapters.mcp.MCPAdapter", _ReadyAdapter)
    result = runner.invoke(main, ["-o", "json", "health", "--agent", "demo"])
    assert result.exit_code == 0, result.output
    assert "✓ READY" in result.output


def test_health_mcp_error(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    _patch_make_client(monkeypatch, MagicMock())
    monkeypatch.setattr("orchestra.adapters.mcp.MCPAdapter", _BoomAdapter)
    result = runner.invoke(main, ["health", "--agent", "demo"])
    assert result.exit_code == 0, result.output
    assert "✗ RuntimeError" in result.output


def test_health_mcp_timeout(runner, monkeypatch):
    _patch_profiles(monkeypatch, {"demo": _PROFILE})
    _patch_make_client(monkeypatch, MagicMock())
    monkeypatch.setattr("orchestra.adapters.mcp.MCPAdapter", _TimeoutAdapter)
    result = runner.invoke(main, ["health", "--agent", "demo"])
    assert result.exit_code == 0, result.output
    assert "✗ timeout" in result.output


# ────────────────────────── chaos ──────────────────────────


def test_chaos_kill_agent(runner, monkeypatch):
    calls = []

    def _fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="stopped worker-walnut\n", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    result = runner.invoke(main, ["chaos", "kill-agent", "walnut", "--during", "stage=code"])
    assert result.exit_code == 0, result.output
    assert "stopped worker-walnut" in result.output
    assert calls[0][0][0][:3] == ["docker", "compose", "stop"]


def test_chaos_kill_agent_stderr(runner, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="container not found\n"),
    )
    result = runner.invoke(main, ["chaos", "kill-agent", "ghost"])
    assert result.exit_code == 0, result.output
    assert "container not found" in result.output


def test_chaos_kill_temporal(runner, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(stdout="", stderr=""))
    result = runner.invoke(main, ["chaos", "kill-temporal", "--during", "stage=code"])
    assert result.exit_code == 0, result.output
    assert "temporal-server restart requested" in result.output


def test_chaos_network_partition(runner):
    result = runner.invoke(main, ["chaos", "network-partition", "walnut", "--duration", "10s"])
    assert result.exit_code == 0, result.output
    assert "network-partition walnut for 10s" in result.output


# ────────────────────────── agents ──────────────────────────


def test_agents_list_json(runner):
    result = runner.invoke(main, ["-o", "json", "agents", "list"])
    assert result.exit_code == 0, result.output
    assert "walnut" in result.output


def test_agents_list_yaml(runner):
    result = runner.invoke(main, ["-o", "yaml", "agents", "list"])
    assert result.exit_code == 0, result.output
    assert "name:" in result.output


def test_agents_drain_and_resume(runner):
    drain = runner.invoke(main, ["agents", "drain", "walnut"])
    assert drain.exit_code == 0, drain.output
    assert "[drain] walnut" in drain.output
    resume = runner.invoke(main, ["agents", "resume", "walnut"])
    assert resume.exit_code == 0, resume.output
    assert "[resume] walnut" in resume.output


# ────────────────────────── submit / validate ──────────────────────────


def test_submit_success(runner, monkeypatch):
    handle = SimpleNamespace(id="wf-1", result_run_id="run-1")
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=handle)
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["submit", MINIMAL, "--param", "task=hello", "--priority", "high"])
    assert result.exit_code == 0, result.output
    assert "✓ submitted" in result.output
    assert "priority    : high" in result.output
    client.start_workflow.assert_awaited_once()


def test_submit_connect_failure(runner, monkeypatch):
    monkeypatch.setattr(
        "orchestra.worker.registry.make_client", AsyncMock(side_effect=RuntimeError("down"))
    )
    result = runner.invoke(main, ["submit", MINIMAL, "--param", "task=hello"])
    assert result.exit_code == 0, result.output
    assert "无法连接 Temporal" in result.output


def test_submit_start_failure(runner, monkeypatch):
    client = MagicMock()
    client.start_workflow = AsyncMock(side_effect=RuntimeError("already exists"))
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["submit", MINIMAL, "--param", "task=hello"])
    assert result.exit_code == 0, result.output
    assert "提交失败" in result.output


def test_status_list_caps_at_twenty(runner, monkeypatch):
    client = MagicMock()
    client.list_workflows = MagicMock(return_value=_aiter([_wf(f"wf-{i:02d}") for i in range(25)]))
    _patch_make_client(monkeypatch, client)
    result = runner.invoke(main, ["-o", "json", "status"])
    assert result.exit_code == 0, result.output
    assert "wf-19" in result.output
    assert "wf-20" not in result.output


def test_status_detail_describe_failure(runner, monkeypatch):
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RuntimeError("gone"))
    _patch_make_client(monkeypatch, _workflow_client(handle))
    result = runner.invoke(main, ["status", "wf-1"])
    assert result.exit_code == 0, result.output
    assert "获取状态失败" in result.output


def test_re_run_stage_without_following_task(runner, monkeypatch):
    client = _rerun_client([_activity_scheduled(10, "code")])
    _patch_cli_client(monkeypatch, client)
    result = runner.invoke(main, ["re-run", "wf-1", "--from-stage", "code", "--yes"])
    assert result.exit_code == 0, result.output
    assert "找不到可重置" in result.output


def test_dry_run_invalid_dag(runner, tmp_path):
    cyc = tmp_path / "cycle.yaml"
    cyc.write_text(
        """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata: {name: cyc, namespace: default, version: 0.1.0}
spec:
  description: x
  agents:
    a: {role: developer, capabilities: [python], mcpEndpoint: "mcp://localhost:1"}
  pipeline:
    stages:
      - {name: s1, agent: a, dependsOn: [s2], output: "$.s1"}
      - {name: s2, agent: a, dependsOn: [s1], output: "$.s2"}
  parameters:
    - {name: task, type: string, required: true}
"""
    )
    result = runner.invoke(main, ["dry-run", str(cyc)])
    assert result.exit_code != 0
    assert "ERROR" in result.output


def test_agents_list_empty_rows(runner):
    result = runner.invoke(main, ["agents", "list", "--label", "team=nonexistent"])
    assert result.exit_code == 0, result.output
    assert "(no results)" in result.output


def test_validate_semantic_invalid(runner, tmp_path):
    bad = tmp_path / "semantic.yaml"
    bad.write_text(
        """
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: bad-pipeline
  namespace: default
  version: 0.1.0
spec:
  description: x
  agents:
    walnut:
      role: developer
      capabilities: [python]
      mcpEndpoint: "mcp://localhost:1"
  pipeline:
    stages:
      - name: s1
        agent: nonexistent_agent
        output: "$.s1"
  parameters:
    - name: task
      type: string
      required: true
"""
    )
    result = runner.invoke(main, ["validate", str(bad)])
    assert result.exit_code != 0
    assert "INVALID" in result.output
