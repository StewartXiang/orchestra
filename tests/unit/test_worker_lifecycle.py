"""单元测试：Worker 生命周期、进程入口与 Client 工厂。

不连接真实 Temporal / 网络：
  - startup probe 的 httpx 用替身；
  - 信号处理 / asyncio 等待全部 mock，测试不会挂起（wait_for 兜底）；
  - worker.main 的 client / worker / 存储入口全部 monkeypatch，绝不真正启动 Worker。

参见 docs/architecture.md "Worker 生命周期"。
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.worker import lifecycle

# ──────────────────────────────────────────────
# 替身
# ──────────────────────────────────────────────


class _FakeLoop:
    """只记录 add_signal_handler 注册结果的假事件循环。"""

    def __init__(self) -> None:
        self.handlers: dict[int, object] = {}

    def add_signal_handler(self, sig: int, callback: object) -> None:
        self.handlers[sig] = callback


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeAsyncClient:
    """httpx.AsyncClient 替身：按 handler 决定每个 URL 的响应或异常。"""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.requested: list[str] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str):
        self.requested.append(url)
        result = self._handler(url)
        if isinstance(result, Exception):
            raise result
        return result


def _install_fake_http(monkeypatch, handler) -> _FakeAsyncClient:
    client = _FakeAsyncClient(handler)
    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", lambda *a, **k: client)
    return client


# ──────────────────────────────────────────────
# 信号处理 / shutdown_event
# ──────────────────────────────────────────────


def test_install_signal_handlers_sets_shutdown_event(monkeypatch):
    loop = _FakeLoop()
    event = asyncio.Event()
    logger = MagicMock()

    monkeypatch.setattr(lifecycle, "asyncio", SimpleNamespace(get_event_loop=lambda: loop))
    monkeypatch.setattr(lifecycle, "_SHUTDOWN_EVENT", event)
    monkeypatch.setattr(lifecycle, "logger", logger)

    lifecycle.install_signal_handlers()

    assert set(loop.handlers) == {signal.SIGTERM, signal.SIGINT}
    assert not event.is_set()

    loop.handlers[signal.SIGINT]()  # type: ignore[operator]
    assert event.is_set()
    logger.info.assert_called_once_with("signal_received", signal="SIGINT")


def test_shutdown_event_returns_module_event(monkeypatch):
    event = asyncio.Event()
    monkeypatch.setattr(lifecycle, "_SHUTDOWN_EVENT", event)
    assert lifecycle.shutdown_event() is event


# ──────────────────────────────────────────────
# startup_probe
# ──────────────────────────────────────────────


async def test_startup_probe_health_200(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "logger", logger)
    client = _install_fake_http(monkeypatch, lambda url: _FakeResponse(200))

    assert await lifecycle.startup_probe("mcp://agent:9000/") is True
    assert client.requested == ["http://agent:9000/health"]
    assert logger.info.call_args.args[0] == "startup_probe_ok"


async def test_startup_probe_404_counts_as_reachable(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "logger", logger)
    client = _install_fake_http(monkeypatch, lambda url: _FakeResponse(404))

    assert await lifecycle.startup_probe("http://agent:9000") is True
    assert client.requested == ["http://agent:9000/health"]
    assert logger.info.call_args.args[0] == "startup_probe_reachable"


async def test_startup_probe_falls_through_to_later_path(monkeypatch):
    def handler(url: str):
        if url.endswith("/health"):
            return _FakeResponse(500)
        if url.endswith("/ping"):
            return lifecycle.httpx.ConnectError("refused")
        return _FakeResponse(200)

    client = _install_fake_http(monkeypatch, handler)

    assert await lifecycle.startup_probe("http://agent:9000") is True
    assert client.requested == [
        "http://agent:9000/health",
        "http://agent:9000/ping",
        "http://agent:9000/",
    ]


async def test_startup_probe_all_paths_fail(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "logger", logger)
    client = _install_fake_http(monkeypatch, lambda url: lifecycle.httpx.ConnectError("refused"))

    assert await lifecycle.startup_probe("http://agent:9000") is False
    assert len(client.requested) == 3
    assert logger.warning.call_args.args[0] == "startup_probe_no_response"


async def test_startup_probe_non_http_error_hits_outer_guard(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "logger", logger)
    _install_fake_http(monkeypatch, lambda url: ValueError("unexpected"))

    assert await lifecycle.startup_probe("http://agent:9000") is False
    assert logger.warning.call_args.args[0] == "startup_probe_error"


async def test_startup_probe_client_construction_failure(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "logger", logger)
    monkeypatch.setattr(
        lifecycle.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("no transport"))
    )

    assert await lifecycle.startup_probe("http://agent:9000") is False
    assert logger.warning.call_args.args[0] == "startup_probe_error"


# ──────────────────────────────────────────────
# wait_for_shutdown
# ──────────────────────────────────────────────


async def test_wait_for_shutdown_graceful(monkeypatch):
    event = asyncio.Event()
    event.set()
    logger = MagicMock()
    monkeypatch.setattr(lifecycle, "_SHUTDOWN_EVENT", event)
    monkeypatch.setattr(lifecycle, "_GRACEFUL_SHUTDOWN_TIMEOUT", 5)
    monkeypatch.setattr(lifecycle, "logger", logger)

    worker = MagicMock()
    worker.shutdown = AsyncMock(return_value=None)

    await asyncio.wait_for(lifecycle.wait_for_shutdown(worker), timeout=2.0)

    worker.shutdown.assert_awaited_once()
    events = [call.args[0] for call in logger.info.call_args_list]
    assert events == ["graceful_shutdown_started", "graceful_shutdown_completed"]


async def test_wait_for_shutdown_timeout_is_swallowed(monkeypatch):
    event = asyncio.Event()
    event.set()
    logger = MagicMock()
    blocker = asyncio.Event()

    async def _hang() -> None:
        await blocker.wait()

    monkeypatch.setattr(lifecycle, "_SHUTDOWN_EVENT", event)
    monkeypatch.setattr(lifecycle, "_GRACEFUL_SHUTDOWN_TIMEOUT", 0.01)
    monkeypatch.setattr(lifecycle, "logger", logger)

    worker = MagicMock()
    worker.shutdown = _hang

    # 外层 wait_for 兜底：即使内部超时逻辑失效，测试也不会挂起
    await asyncio.wait_for(lifecycle.wait_for_shutdown(worker), timeout=2.0)

    warnings = [call.args[0] for call in logger.warning.call_args_list]
    assert warnings == ["graceful_shutdown_timeout"]


# ──────────────────────────────────────────────
# worker.main
# ──────────────────────────────────────────────


@pytest.fixture
def worker_main(monkeypatch):
    """隔离 worker.main：替换所有 IO / Temporal 入口。"""
    import orchestra.adapters.registry as adapters_registry
    import orchestra.observability.audit as audit_obs
    import orchestra.observability.logging as logging_obs
    import orchestra.observability.metrics as metrics_obs
    import orchestra.observability.tracing as tracing_obs
    import orchestra.state.artifact_store as artifact_state
    import orchestra.state.idempotency as idem_state
    import orchestra.worker.lifecycle as lifecycle_mod
    import orchestra.worker.main as main_mod
    import orchestra.worker.registry as worker_registry

    logger = MagicMock()
    gauge = MagicMock()

    monkeypatch.setenv("PROFILE_NAME", "walnut")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MCP_ENDPOINT", raising=False)

    monkeypatch.setattr(logging_obs, "configure_logging", MagicMock())
    monkeypatch.setattr(logging_obs, "get_logger", MagicMock(return_value=logger))
    monkeypatch.setattr(tracing_obs, "init_tracing", MagicMock())
    monkeypatch.setattr(metrics_obs, "start_metrics_server", MagicMock())
    monkeypatch.setattr(metrics_obs, "temporal_worker_alive", gauge)
    monkeypatch.setattr(idem_state, "init_store", MagicMock())
    monkeypatch.setattr(artifact_state, "init_artifact_store", MagicMock())
    monkeypatch.setattr(audit_obs, "init_audit_writer", MagicMock())

    profile = SimpleNamespace(mcpEndpoint="mcp://agent:9000", taskQueue="q-walnut")
    loader = MagicMock(return_value={"walnut": profile})
    monkeypatch.setattr(adapters_registry, "load_profiles_from_yaml", loader)
    monkeypatch.setattr(adapters_registry, "build_registry", MagicMock())

    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(lifecycle_mod, "startup_probe", probe)
    monkeypatch.setattr(lifecycle_mod, "install_signal_handlers", MagicMock())
    monkeypatch.setattr(lifecycle_mod, "wait_for_shutdown", AsyncMock(return_value=None))

    fake_worker = MagicMock()
    fake_worker.run = AsyncMock(return_value=None)
    make_client = AsyncMock(return_value=MagicMock())
    build_worker = MagicMock(return_value=fake_worker)
    monkeypatch.setattr(worker_registry, "make_client", make_client)
    monkeypatch.setattr(worker_registry, "build_worker", build_worker)

    return SimpleNamespace(
        main=main_mod.main,
        logger=logger,
        gauge=gauge,
        probe=probe,
        loader=loader,
        make_client=make_client,
        build_worker=build_worker,
        fake_worker=fake_worker,
        init_store=idem_state.init_store,
        init_artifact_store=artifact_state.init_artifact_store,
        init_tracing=tracing_obs.init_tracing,
        start_metrics_server=metrics_obs.start_metrics_server,
        profile=profile,
    )


async def test_main_happy_path_uses_sqlite(worker_main):
    await worker_main.main()

    worker_main.probe.assert_awaited_once_with("http://agent:9000")
    worker_main.make_client.assert_awaited_once_with("localhost:7233", namespace="default")
    worker_main.build_worker.assert_called_once()
    assert worker_main.build_worker.call_args.args[1] == "q-walnut"
    assert worker_main.fake_worker.run.await_count == 1
    worker_main.init_store.assert_called_once_with("sqlite", db_path="idempotency-walnut.db")
    assert worker_main.gauge.labels.call_args_list[0].kwargs == {"profile": "walnut"}
    assert [call.args[0] for call in worker_main.gauge.labels.return_value.set.call_args_list] == [
        1,
        0,
    ]


async def test_main_honors_env_overrides_and_redis(worker_main, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MCP_ENDPOINT", "mcp://override:1234")
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "prod")
    monkeypatch.setenv("METRICS_PORT", "9201")
    monkeypatch.setenv("ARTIFACTS_BASE_PATH", "/tmp/artifacts")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4317")

    await worker_main.main()

    worker_main.probe.assert_awaited_once_with("http://override:1234")
    worker_main.make_client.assert_awaited_once_with("temporal:7233", namespace="prod")
    worker_main.init_store.assert_called_once_with("redis", redis_url="redis://localhost:6379/0")
    worker_main.init_artifact_store.assert_called_once_with("/tmp/artifacts")
    worker_main.start_metrics_server.assert_called_once_with(port=9201)
    worker_main.init_tracing.assert_called_once_with(
        service_name="orchestra-worker-walnut", otlp_endpoint="http://otel:4317"
    )


async def test_main_exits_when_profile_not_found(worker_main):
    worker_main.loader.return_value = {}

    with pytest.raises(SystemExit) as exc:
        await worker_main.main()

    assert exc.value.code == 1
    assert worker_main.logger.error.call_args.args[0] == "profile_not_found"
    worker_main.make_client.assert_not_awaited()


async def test_main_exits_when_startup_probe_fails(worker_main):
    worker_main.probe.return_value = False

    with pytest.raises(SystemExit) as exc:
        await worker_main.main()

    assert exc.value.code == 1
    assert worker_main.logger.error.call_args.args[0] == "startup_probe_failed"
    worker_main.make_client.assert_not_awaited()


async def test_main_requires_profile_name(monkeypatch):
    from orchestra.worker.main import main

    monkeypatch.delenv("PROFILE_NAME", raising=False)

    with pytest.raises(SystemExit) as exc:
        await main()

    assert exc.value.code == 1


def test_preload_sandbox_unsafe_modules_is_idempotent():
    from orchestra.worker.main import _preload_sandbox_unsafe_modules

    _preload_sandbox_unsafe_modules()


def test_preload_suppresses_import_error(monkeypatch):
    import builtins

    from orchestra.worker.main import _preload_sandbox_unsafe_modules

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    _preload_sandbox_unsafe_modules()  # 不应抛异常


# ──────────────────────────────────────────────
# cli.client / worker.registry
# ──────────────────────────────────────────────


async def test_get_client_delegates_to_registry(monkeypatch):
    import orchestra.cli.client as cli_client
    import orchestra.worker.registry as worker_registry

    make_client = AsyncMock(return_value="client-obj")
    monkeypatch.setattr(worker_registry, "make_client", make_client)

    result = await cli_client.get_client("temporal:7233", "ns")

    assert result == "client-obj"
    make_client.assert_awaited_once_with("temporal:7233", "ns")


def test_build_workflow_runner_unsandboxed():
    from temporalio.worker import UnsandboxedWorkflowRunner

    from orchestra.worker.registry import build_workflow_runner

    assert isinstance(build_workflow_runner(unsandboxed=True), UnsandboxedWorkflowRunner)


def test_build_workflow_runner_sandboxed():
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from orchestra.worker.registry import build_workflow_runner

    assert isinstance(build_workflow_runner(), SandboxedWorkflowRunner)


async def test_make_client_falls_back_to_default_converter(monkeypatch):
    from temporalio.client import Client

    from orchestra.worker.registry import make_client

    # 让 pydantic converter 导入失败，覆盖 DataConverter.default 回退分支
    monkeypatch.setitem(sys.modules, "temporalio.contrib.pydantic", None)
    monkeypatch.delenv("ORCHESTRA_ENCRYPTION_KEY", raising=False)

    connect = AsyncMock(return_value="client-obj")
    monkeypatch.setattr(Client, "connect", connect)

    result = await make_client("temporal:7233", "ns")

    assert result == "client-obj"
    _, kwargs = connect.call_args
    assert "data_converter" in kwargs
