"""Worker 进程入口。

Pydantic / Python stdlib 等模块的 import 会触发 os.environ 访问，
必须在 Temporal 沙箱初始化前预加载，否则 workflow 确定性检查会报错。

用法::

    python -m orchestra.worker.main

环境变量：
  PROFILE_NAME         必填，对应 config/profiles.yaml 中的 profile 名
  MCP_ENDPOINT         可选，覆盖 profile 中的 mcpEndpoint
  TEMPORAL_HOST        Temporal Server 地址（默认 localhost:7233）
  TEMPORAL_NAMESPACE   Temporal Namespace（默认 default）
  METRICS_PORT         Prometheus 指标端口（默认 9100）
  LOG_LEVEL            日志级别（默认 INFO）
  ORCHESTRA_ENCRYPTION_KEY  32 字节十六进制加密密钥（可选）
  REDIS_URL            Redis URL（可选，默认用 SQLite 幂等键存储）
  ARTIFACTS_BASE_PATH  Artifact 存储根目录（默认 /opt/agent-orchestra/artifacts）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 确保 src 在 Python 路径中（直接执行时 / Docker 自适应）
_p = Path(__file__).resolve()
try:
    sys.path.insert(0, str(_p.parents[min(4, len(_p.parents) - 1)]))
except Exception:
    pass

# ⚠️ 在 Temporal 沙箱初始化前预加载所有会访问 os.environ 的模块
# 这些模块的 import 顶层代码会调用 os.environ.get()，如果被 Temporal 沙箱拦截会报
# RestrictedWorkflowAccessError。预加载后它们已在 sys.modules 中，沙箱不再拦截。
def _preload_sandbox_unsafe_modules() -> None:
    """预加载 Pydantic 及其传递依赖（import 时访问 os.environ/zoneinfo）。"""
    to_load = [
        "pydantic", "pydantic.plugin", "pydantic.plugin._loader",
        "pydantic._internal", "pydantic.deprecated", "pydantic_core",
        "zoneinfo", "sysconfig", "platform", "io", "pathlib",
        "json", "yaml", "datetime", "re", "typing",
    ]
    for mod in to_load:
        try:
            __import__(mod)
        except ImportError:
            pass
    # 再 import orchestra domain（会触发 pydantic model 验证）
    try:
        import orchestra.domain.pipeline  # noqa: F401
    except ImportError:
        pass

_preload_sandbox_unsafe_modules()


async def main() -> None:
    profile_name = os.environ.get("PROFILE_NAME", "")
    if not profile_name:
        print("ERROR: PROFILE_NAME 环境变量未设置", file=sys.stderr)
        sys.exit(1)

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    metrics_port = int(os.environ.get("METRICS_PORT", "9100"))
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    artifacts_path = os.environ.get("ARTIFACTS_BASE_PATH", "/opt/agent-orchestra/artifacts")
    redis_url = os.environ.get("REDIS_URL", "")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    # 初始化日志
    from ..observability.logging import configure_logging
    configure_logging(level=log_level)
    from ..observability.logging import get_logger
    logger = get_logger(__name__)

    # 初始化 OTel
    from ..observability.tracing import init_tracing
    init_tracing(service_name=f"orchestra-worker-{profile_name}", otlp_endpoint=otlp_endpoint or None)

    # 初始化 Prometheus
    from ..observability.metrics import start_metrics_server
    start_metrics_server(port=metrics_port)

    # 初始化幂等键存储
    from ..state.idempotency import init_store
    if redis_url:
        init_store("redis", redis_url=redis_url)
        logger.info("idempotency_store", backend="redis")
    else:
        init_store("sqlite", db_path=f"idempotency-{profile_name}.db")
        logger.info("idempotency_store", backend="sqlite")

    # 初始化 Artifact 存储
    from ..state.artifact_store import init_artifact_store
    init_artifact_store(artifacts_path)

    # 初始化审计日志
    from ..observability.audit import init_audit_writer
    init_audit_writer(f"audits-{profile_name}.db")

    # 加载 profiles（所有 worker 加载全部 9 个 profile，
    # 因为 pipeline workflow 将所有 activity 发到同一 task queue）
    from ..adapters.registry import build_registry, load_profiles_from_yaml
    # Docker: files at /app/config; dev: at <repo>/config
    _candidates = [
        Path("/app/config"),
        Path(__file__).resolve().parents[3] / "config",
    ]
    config_dir = next((d for d in _candidates if d.is_dir()), _candidates[0])
    profiles = load_profiles_from_yaml(config_dir / "profiles.yaml")

    if profile_name not in profiles:
        logger.error("profile_not_found", profile=profile_name, available=list(profiles.keys()))
        sys.exit(1)

    profile = profiles[profile_name]
    mcp_endpoint = os.environ.get("MCP_ENDPOINT", profile.mcpEndpoint)
    task_queue = profile.taskQueue or f"agent-{profile_name}"

    logger.info("worker_starting", profile=profile_name, task_queue=task_queue, temporal=temporal_host)

    # ⚠️ 将所有 profile 的 MCP endpoint 从 mcp://host:187xx 映射为 http://host:189xx
    # 必须在 startup probe 之前完成（probe 使用映射后的端点）
    for pname, p in profiles.items():
        p.mcpEndpoint = p.mcpEndpoint.replace("mcp://", "http://").replace(":187", ":189")
    mcp_endpoint = mcp_endpoint.replace("mcp://", "http://").replace(":187", ":189")
    build_registry(profiles)

    # Startup probe（使用映射后的端点）
    from ..worker.lifecycle import install_signal_handlers, startup_probe, wait_for_shutdown
    if not await startup_probe(mcp_endpoint):
        logger.error("startup_probe_failed", profile=profile_name)
        sys.exit(1)

    # 连接 Temporal（带 pydantic_data_converter）
    from ..worker.registry import build_worker, make_client
    client = await make_client(temporal_host, namespace=namespace)
    logger.info("temporal_connected", host=temporal_host, namespace=namespace)

    # 构建并启动 Worker
    worker = build_worker(client, task_queue)

    install_signal_handlers()

    from ..observability.metrics import temporal_worker_alive
    temporal_worker_alive.labels(profile=profile_name).set(1)

    logger.info("worker_ready", profile=profile_name, task_queue=task_queue)

    try:
        await asyncio.gather(
            worker.run(),
            wait_for_shutdown(worker),
            return_exceptions=True,
        )
    finally:
        temporal_worker_alive.labels(profile=profile_name).set(0)
        logger.info("worker_stopped", profile=profile_name)


if __name__ == "__main__":
    asyncio.run(main())
