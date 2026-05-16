"""Worker 生命周期：startup probe + 优雅关闭。

startup probe（Worker 启动时调用）：
  1. MCP endpoint 可达（GET /health 200）
  2. 如果设置了 LLM_API_KEY_CHECK，验证模型 API 可调
  通过后才注册到 Task Queue

优雅关闭（SIGTERM 处理）：
  1. 停止从 Task Queue 拉取新任务
  2. 等待当前 Activity 完成（最多 60s）
  3. 超时后 Activity 心跳停止，Temporal 重试到副本
  4. 进程退出
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import httpx

from ..observability.logging import get_logger

logger = get_logger(__name__)

_SHUTDOWN_EVENT = asyncio.Event()
_GRACEFUL_SHUTDOWN_TIMEOUT = int(os.environ.get("GRACEFUL_SHUTDOWN_TIMEOUT", "60"))


def install_signal_handlers() -> None:
    """安装 SIGTERM / SIGINT 处理器（Worker 进程调用）。"""
    loop = asyncio.get_event_loop()

    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("signal_received", signal=sig.name)
        _SHUTDOWN_EVENT.set()

    loop.add_signal_handler(signal.SIGTERM, lambda: _handle_signal(signal.SIGTERM))
    loop.add_signal_handler(signal.SIGINT, lambda: _handle_signal(signal.SIGINT))


def shutdown_event() -> asyncio.Event:
    return _SHUTDOWN_EVENT


async def startup_probe(mcp_endpoint: str, *, timeout: float = 10.0) -> bool:
    """检查 Agent MCP 服务可达（startup probe）。

    :returns: True = 健康，False = 不健康（Worker 不注册到 Task Queue）
    """
    health_url = mcp_endpoint.rstrip("/").replace("mcp://", "http://") + "/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(health_url)
            if resp.status_code == 200:
                logger.info("startup_probe_ok", endpoint=mcp_endpoint)
                return True
            logger.warning("startup_probe_failed", endpoint=mcp_endpoint, status=resp.status_code)
            return False
    except Exception as e:
        logger.warning("startup_probe_error", endpoint=mcp_endpoint, error=str(e))
        return False


async def wait_for_shutdown(worker: "temporalio.worker.Worker") -> None:
    """等待关闭信号，然后优雅停止 Worker。"""
    await _SHUTDOWN_EVENT.wait()
    logger.info("graceful_shutdown_started", timeout=_GRACEFUL_SHUTDOWN_TIMEOUT)
    try:
        await asyncio.wait_for(worker.shutdown(), timeout=_GRACEFUL_SHUTDOWN_TIMEOUT)
        logger.info("graceful_shutdown_completed")
    except asyncio.TimeoutError:
        logger.warning("graceful_shutdown_timeout", timeout=_GRACEFUL_SHUTDOWN_TIMEOUT)
