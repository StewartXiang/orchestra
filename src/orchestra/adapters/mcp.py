"""MCP AgentAdapter 实现。

与 Agent 通过 MCP over HTTP/SSE 通信：
  POST {mcpEndpoint}/tools/call  → 执行工具
  GET  {mcpEndpoint}/health      → 健康检查
  GET  {mcpEndpoint}/capabilities → 能力声明

每次 execute_task 由一系列 MCP tool_call 组成；具体调用序列由 Agent 内部决定。
心跳通过 on_heartbeat 回调传给 Activity 层（不由 Adapter 直接向 Temporal 心跳）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any, Callable

import httpx

from ..domain.agent import AgentCapabilities, AgentHealth, AgentMetrics, Role
from ..domain.enums import HealthStatus
from ..domain.errors import AuthError, MCPDisconnect, TimeoutError, TransientError
from ..domain.state import Checkpoint, ProgressInfo, TaskInput, TaskOutput
from .sandbox import Sandbox


class MCPAdapter:
    """MCP 协议 AgentAdapter 实现。"""

    def __init__(
        self,
        profile_name: str,
        mcp_endpoint: str,
        allowed_tools: list[str],
        role: Role = Role.DEVELOPER,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._name = profile_name
        self._endpoint = mcp_endpoint.rstrip("/").replace("mcp://", "http://")
        self._role = role
        self._sandbox = Sandbox(allowed_tools=allowed_tools, profile_name=profile_name)
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def execute_task(
        self,
        task: TaskInput,
        on_heartbeat: Callable[[ProgressInfo], None] | None = None,
        resume_from: Checkpoint | None = None,
    ) -> TaskOutput:
        start = time.monotonic()
        client = await self._get_client()

        # 构造 MCP 请求（将 Orchestra TaskInput 映射为 MCP execute 格式）
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if task.traceparent:
            headers["traceparent"] = task.traceparent

        payload: dict[str, Any] = {
            "task_id": task.idempotency_key,
            "stage": task.stage_name,
            "role": task.role,
            "tools": task.tools,
            "input": task.input,
        }
        if task.prompt:
            payload["prompt"] = task.prompt
        if task.output_schema:
            payload["output_schema"] = task.output_schema
            # response_tool 让 Agent 以 tool-call 方式提交结构化结果
            # LLM 对 tool calling 的格式遵守度远高于自由 JSON
            payload["response_tool"] = {
                "name": "submit_result",
                "description": (
                    "完成任务后调用此 tool 提交最终结果。"
                    "参数必须严格匹配 output_schema。不要返回自由文本，必须调用此 tool。"
                ),
                "parameters": task.output_schema,
            }
        if resume_from:
            payload["resume_from"] = {
                "step": resume_from.step,
                "progress": resume_from.progress,
                "data": resume_from.data,
            }

        if on_heartbeat:
            on_heartbeat(ProgressInfo(
                stage=task.stage_name, phase="started", progress=0, attempt=1
            ))

        try:
            response = await client.post(
                f"{self._endpoint}/execute",
                json=payload,
                headers=headers,
            )
        except httpx.ConnectError as e:
            raise MCPDisconnect(f"MCP 连接失败 [{self._name}]: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutError(f"MCP 请求超时 [{self._name}]: {e}") from e
        except httpx.RequestError as e:
            raise TransientError(f"MCP 请求错误 [{self._name}]: {e}") from e

        _raise_for_status(response, self._name)

        data = response.json()

        if on_heartbeat:
            on_heartbeat(ProgressInfo(
                stage=task.stage_name, phase="completing", progress=100, attempt=1
            ))

        duration = time.monotonic() - start
        return TaskOutput(
            output=data.get("output"),
            tokens_consumed=data.get("tokens_consumed", 0),
            cost_usd=data.get("cost_usd", 0.0),
            duration_seconds=duration,
        )

    async def check_health(self) -> AgentHealth:
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._endpoint}/health", timeout=5.0)
            if resp.status_code == 200:
                return AgentHealth(status=HealthStatus.READY, message="ok")
            return AgentHealth(status=HealthStatus.NOT_READY, message=f"HTTP {resp.status_code}")
        except Exception as e:
            return AgentHealth(status=HealthStatus.DEAD, message=str(e))

    async def cancel_task(self, task_id: str, grace_period: timedelta) -> None:
        client = await self._get_client()
        try:
            await asyncio.wait_for(
                client.post(f"{self._endpoint}/cancel", json={"task_id": task_id}),
                timeout=grace_period.total_seconds(),
            )
        except (asyncio.TimeoutError, Exception):
            pass  # 取消尽力而为

    async def get_capabilities(self) -> AgentCapabilities:
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._endpoint}/capabilities", timeout=10.0)
            _raise_for_status(resp, self._name)
            data = resp.json()
            return AgentCapabilities(
                role=Role(data.get("role", self._role.value)),
                capabilities=data.get("capabilities", []),
                tools=data.get("tools", []),
                model=data.get("model"),
                version=data.get("version"),
            )
        except MCPDisconnect:
            raise
        except Exception as e:
            raise TransientError(f"get_capabilities 失败 [{self._name}]: {e}") from e

    async def get_metrics(self) -> AgentMetrics:
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._endpoint}/metrics", timeout=5.0)
            if resp.status_code != 200:
                return AgentMetrics()
            data = resp.json()
            return AgentMetrics(
                busy_slots=data.get("busy_slots", 0),
                queue_depth=data.get("queue_depth", 0),
                tokens_consumed_total=data.get("tokens_consumed_total", 0),
            )
        except Exception:
            return AgentMetrics()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _raise_for_status(response: httpx.Response, profile: str) -> None:
    if response.status_code == 401:
        raise AuthError(f"MCP 认证失败 [{profile}]")
    if response.status_code == 403:
        raise AuthError(f"MCP 权限不足 [{profile}]")
    if response.status_code >= 500:
        raise TransientError(f"MCP 服务端错误 [{profile}]: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise TransientError(f"MCP 请求失败 [{profile}]: HTTP {response.status_code}")
