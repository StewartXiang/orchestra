"""Workflow / Activity / DataConverter 注册。

DataConverter 位于 Client 层（Client.connect 时传入），Worker 自动继承。
"""

from __future__ import annotations

from temporalio.worker import Worker

from ..activities import ALL_ACTIVITIES
from ..workflows import ALL_WORKFLOWS


def build_worker(
    client: "temporalio.client.Client",
    task_queue: str,
    *,
    max_concurrent_activities: int = 5,
) -> Worker:
    """构建 Worker 实例（含所有 Workflow + Activity）。

    DataConverter 由 client 携带（使用 make_client() 创建带正确 converter 的 Client）。
    """
    return Worker(
        client,
        task_queue=task_queue,
        workflows=ALL_WORKFLOWS,
        activities=ALL_ACTIVITIES,
        max_concurrent_activities=max_concurrent_activities,
    )


async def make_client(
    host: str = "localhost:7233",
    namespace: str = "default",
) -> "temporalio.client.Client":
    """创建带 Pydantic v2 DataConverter 的 Temporal Client。"""
    from temporalio.client import Client

    try:
        from temporalio.contrib.pydantic import pydantic_data_converter  # type: ignore[import-untyped]
        return await Client.connect(
            host,
            namespace=namespace,
            data_converter=pydantic_data_converter,
        )
    except ImportError:
        pass

    return await Client.connect(host, namespace=namespace)
