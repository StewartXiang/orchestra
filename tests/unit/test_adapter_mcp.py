"""单元测试：MCPAdapter（httpx.MockTransport，不连真实 MCP 服务）。

覆盖 execute_task 的响应契约（output / tool_calls.submit_result / result 兼容）、
错误映射（401/403/5xx/4xx、ConnectError、Timeout）、心跳、健康探针、
能力发现、指标与关闭。
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from orchestra.adapters.mcp import MCPAdapter
from orchestra.domain.enums import HealthStatus
from orchestra.domain.errors import AuthError, MCPDisconnect, TimeoutError, TransientError
from orchestra.domain.state import Checkpoint, TaskInput


def _task(**over: object) -> TaskInput:
    base: dict[str, object] = {
        "workflow_id": "wf-1",
        "stage_name": "code",
        "agent_name": "walnut",
        "role": "developer",
        "tools": ["file_read"],
        "input": {"task": "hi"},
        "idempotency_key": "wf-1/code",
    }
    base.update(over)
    return TaskInput(**base)  # type: ignore[arg-type]


def _adapter(handler, **kw: object) -> MCPAdapter:
    adapter = MCPAdapter(
        "walnut",
        "mcp://127.0.0.1:18761",
        ["file_read", "file_write"],
        **kw,  # type: ignore[arg-type]
    )
    # 注入 MockTransport 客户端；_get_client() 会复用它（未 closed）
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


# ─────────────── execute_task：响应契约 ───────────────


async def test_execute_task_output_envelope() -> None:
    adapter = _adapter(
        lambda r: httpx.Response(
            200, json={"output": {"patch": "x"}, "tokens_consumed": 7, "cost_usd": 0.02}
        )
    )
    out = await adapter.execute_task(_task())
    assert out.output == {"patch": "x"}
    assert out.tokens_consumed == 7
    assert out.cost_usd == pytest.approx(0.02)


async def test_execute_task_tool_calls_submit_result() -> None:
    adapter = _adapter(
        lambda r: httpx.Response(
            200,
            json={
                "tool_calls": [
                    {"name": "other", "arguments": {"x": 1}},
                    {"name": "submit_result", "arguments": {"verdict": "pass"}},
                ]
            },
        )
    )
    out = await adapter.execute_task(_task())
    assert out.output == {"verdict": "pass"}


async def test_execute_task_legacy_result_key() -> None:
    adapter = _adapter(lambda r: httpx.Response(200, json={"result": 42}))
    assert (await adapter.execute_task(_task())).output == 42


async def test_execute_task_payload_includes_prompt_schema_resume() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"output": 1})

    adapter = _adapter(handler)
    await adapter.execute_task(
        _task(
            traceparent="00-abc-def-01",
            prompt="do {{ task }}",
            output_schema={"type": "object"},
        ),
        on_heartbeat=lambda p: None,
        resume_from=Checkpoint(step="compiling", progress=50.0, data={"n": 1}),
    )
    assert seen["prompt"] == "do {{ task }}"
    assert seen["response_tool"]["name"] == "submit_result"
    assert seen["output_schema"] == {"type": "object"}
    assert seen["resume_from"]["step"] == "compiling"


async def test_execute_task_heartbeat_phases() -> None:
    phases: list[str] = []
    adapter = _adapter(lambda r: httpx.Response(200, json={"output": 1}))
    await adapter.execute_task(_task(), on_heartbeat=lambda p: phases.append(p.phase))
    assert phases == ["started", "completing"]


# ─────────────── execute_task：错误映射 ───────────────


async def test_execute_task_connect_error_maps_to_mcp_disconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(MCPDisconnect):
        await _adapter(handler).execute_task(_task())


async def test_execute_task_timeout_maps_to_domain_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TimeoutError):
        await _adapter(handler).execute_task(_task())


async def test_execute_task_request_error_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("boom")

    with pytest.raises(TransientError):
        await _adapter(handler).execute_task(_task())


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, AuthError), (403, AuthError), (503, TransientError), (400, TransientError)],
)
async def test_execute_task_http_status_mapping(status: int, expected: type[Exception]) -> None:
    adapter = _adapter(lambda r: httpx.Response(status, json={}))
    with pytest.raises(expected):
        await adapter.execute_task(_task())


# ─────────────── 探针 / 能力 / 指标 / 关闭 ───────────────


async def test_check_health_ready_not_ready_dead() -> None:
    assert (
        await _adapter(lambda r: httpx.Response(200)).check_health()
    ).status == HealthStatus.READY
    assert (
        await _adapter(lambda r: httpx.Response(503)).check_health()
    ).status == HealthStatus.NOT_READY

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert (await _adapter(boom).check_health()).status == HealthStatus.DEAD


async def test_cancel_task_is_best_effort() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    await _adapter(boom).cancel_task("t-1", grace_period=timedelta(seconds=1))

    seen: dict = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    await _adapter(ok).cancel_task("t-2", grace_period=timedelta(seconds=1))
    assert seen["url"].endswith("/cancel")


async def test_get_capabilities_ok() -> None:
    adapter = _adapter(
        lambda r: httpx.Response(
            200,
            json={
                "role": "tester",
                "capabilities": ["pytest"],
                "tools": ["pytest_run"],
                "model": "m",
                "version": "1",
            },
        )
    )
    caps = await adapter.get_capabilities()
    assert caps.role.value == "tester"
    assert caps.capabilities == ["pytest"]


async def test_get_capabilities_error_maps_to_transient() -> None:
    adapter = _adapter(lambda r: httpx.Response(500, json={}))
    with pytest.raises(TransientError):
        await adapter.get_capabilities()


async def test_get_metrics_variants() -> None:
    ok = _adapter(lambda r: httpx.Response(200, json={"busy_slots": 2, "queue_depth": 3}))
    metrics = await ok.get_metrics()
    assert metrics.busy_slots == 2 and metrics.queue_depth == 3

    non_200 = _adapter(lambda r: httpx.Response(404))
    assert (await non_200.get_metrics()).busy_slots == 0

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert (await _adapter(boom).get_metrics()).busy_slots == 0


async def test_close_closes_client() -> None:
    adapter = _adapter(lambda r: httpx.Response(200))
    await adapter.close()
    assert adapter._client is not None and adapter._client.is_closed


async def test_endpoint_normalization() -> None:
    adapter = MCPAdapter("w", "mcp://host:1234/", ["file_read"])
    assert adapter._endpoint == "http://host:1234"
