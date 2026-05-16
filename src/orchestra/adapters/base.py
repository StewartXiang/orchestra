"""AgentAdapter Protocol — Agent 通信层唯一公开契约。

扩展规则（来自 adapters/README.md）：
- 只有这个文件定义 Protocol
- 新增 Adapter 只能新增文件（mcp.py / mock.py / ...），不改 base.py
- 新增能力用 mixin 或子 Protocol，不在 AgentAdapter 上加方法
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Protocol, runtime_checkable

from ..domain.agent import AgentCapabilities, AgentHealth, AgentMetrics
from ..domain.state import Checkpoint, ProgressInfo, TaskInput, TaskOutput


@runtime_checkable
class AgentAdapter(Protocol):
    """所有 Agent 适配器必须实现的协议。"""

    async def execute_task(
        self,
        task: TaskInput,
        on_heartbeat: Callable[[ProgressInfo], None] | None = None,
        resume_from: Checkpoint | None = None,
    ) -> TaskOutput:
        """执行 Agent 任务。

        :param task: 由 Workflow 构造的任务输入，含幂等键、tracecontext
        :param on_heartbeat: 心跳回调（Worker 用于更新 Temporal Activity 心跳）
        :param resume_from: 断点续传检查点（重试时由 Activity 传入上次心跳的 checkpoint）
        """
        ...

    async def check_health(self) -> AgentHealth:
        """主动健康检查（readiness probe 调用）。"""
        ...

    async def cancel_task(self, task_id: str, grace_period: timedelta) -> None:
        """取消当前任务。

        :param grace_period: 在此时间内完成清理并返回，超时后 Worker 强制终止
        """
        ...

    async def get_capabilities(self) -> AgentCapabilities:
        """返回 Agent 声明的能力（Worker 启动时与 YAML 比对）。"""
        ...

    async def get_metrics(self) -> AgentMetrics:
        """返回实时指标（busy_slots / queue_depth / token_consumed 等）。"""
        ...
