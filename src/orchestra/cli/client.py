"""Temporal Client 工厂（CLI 用）。"""

from __future__ import annotations
from typing import Any


async def get_client(host: str, namespace: str) -> Any:
    """连接 Temporal Server，使用 pydantic_data_converter（如可用）。"""
    from orchestra.worker.registry import make_client
    return await make_client(host, namespace)
