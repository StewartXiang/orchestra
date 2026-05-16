"""Mock AgentAdapter — 单元 / 集成测试专用。

每次 execute_task 调用根据预设配置决定成功、失败、慢响应或取消。
测试代码通过 MockAgentAdapter(behavior=...) 配置行为，不依赖真实 MCP 服务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable

from ..domain.agent import AgentCapabilities, AgentHealth, AgentMetrics, Role
from ..domain.enums import AgentStatus, HealthStatus
from ..domain.state import Checkpoint, ProgressInfo, TaskInput, TaskOutput


class MockBehavior(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"
    SLOW = "slow"           # 延迟后成功（测试超时）
    CANCEL = "cancel"       # 执行中模拟取消
    HEARTBEAT_STOP = "heartbeat_stop"  # 停止心跳（测试 liveness timeout）


@dataclass
class MockAgentAdapter:
    """可配置行为的 Mock Adapter。

    Usage::

        adapter = MockAgentAdapter(
            profile_name="walnut",
            behavior=MockBehavior.SUCCESS,
            output={"patch": "diff content"},
        )
    """

    profile_name: str
    role: Role = Role.DEVELOPER
    capabilities: list[str] = field(default_factory=lambda: ["python"])
    tools: list[str] = field(default_factory=lambda: ["file_read", "file_write"])
    behavior: MockBehavior = MockBehavior.SUCCESS
    output: dict[str, Any] = field(default_factory=dict)
    error_message: str = "mock error"
    error_code: str = "TransientError"
    slow_seconds: float = 2.0
    heartbeat_count: int = 0  # 记录心跳次数（测试验证用）
    call_count: int = 0
    last_task: TaskInput | None = None

    async def execute_task(
        self,
        task: TaskInput,
        on_heartbeat: Callable[[ProgressInfo], None] | None = None,
        resume_from: Checkpoint | None = None,
    ) -> TaskOutput:
        self.call_count += 1
        self.last_task = task

        if on_heartbeat:
            self.heartbeat_count += 1
            on_heartbeat(ProgressInfo(
                stage=task.stage_name, phase="started", progress=0, attempt=1
            ))

        if self.behavior == MockBehavior.SLOW:
            await asyncio.sleep(self.slow_seconds)

        if self.behavior == MockBehavior.CANCEL:
            raise asyncio.CancelledError("mock cancelled")

        if self.behavior == MockBehavior.HEARTBEAT_STOP:
            # 不再发心跳，模拟 liveness timeout
            await asyncio.sleep(self.slow_seconds)

        if self.behavior == MockBehavior.FAIL:
            raise RuntimeError(self.error_message)

        if on_heartbeat:
            self.heartbeat_count += 1
            on_heartbeat(ProgressInfo(
                stage=task.stage_name, phase="running", progress=50, attempt=1
            ))

        # SUCCESS
        if on_heartbeat:
            self.heartbeat_count += 1
            on_heartbeat(ProgressInfo(
                stage=task.stage_name, phase="completing", progress=100, attempt=1
            ))

        return TaskOutput(
            output=self.output,
            tokens_consumed=100,
            cost_usd=0.001,
            duration_seconds=0.1,
        )

    async def check_health(self) -> AgentHealth:
        return AgentHealth(status=HealthStatus.READY, message="mock healthy")

    async def cancel_task(self, task_id: str, grace_period: timedelta) -> None:
        pass  # mock: 立即完成

    async def get_capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            role=self.role,
            capabilities=self.capabilities,
            tools=self.tools,
            model="mock-model",
            version="0.1.0",
        )

    async def get_metrics(self) -> AgentMetrics:
        return AgentMetrics(busy_slots=0, queue_depth=0)
