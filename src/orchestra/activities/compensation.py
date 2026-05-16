"""Saga 补偿 Activity。

当流水线失败时，按补偿策略（reverse / parallel / custom）反向调用补偿动作。
每个补偿 Activity 也遵循三件套铁律。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from temporalio import activity

from ..adapters.registry import get_adapter
from ..domain.state import TaskInput
from ..observability.logging import get_logger
from ..state.idempotency import get_store

logger = get_logger(__name__)


@dataclass
class CompensationInput:
    for_stage: str
    agent_name: str
    action: str
    input_data: dict


@activity.defn
async def run_compensation(inp: CompensationInput) -> dict:
    """执行单个补偿动作。"""
    # ① 心跳
    activity.heartbeat({"phase": "compensation_started", "for_stage": inp.for_stage})

    info = activity.info()
    idempotency_key = f"compensation/{info.workflow_id}/{inp.for_stage}/{inp.action}"
    store = get_store()

    # ② 幂等查询
    if cached := await store.get(idempotency_key):
        logger.info("compensation_cached", for_stage=inp.for_stage, action=inp.action)
        return cached

    adapter = get_adapter(inp.agent_name)

    task = TaskInput(
        workflow_id=info.workflow_id,
        stage_name=f"compensation_{inp.for_stage}",
        agent_name=inp.agent_name,
        role="ci_engineer",
        tools=[],
        input={"action": inp.action, **inp.input_data},
        idempotency_key=idempotency_key,
    )

    try:
        output = await adapter.execute_task(task)
        result = {"success": True, "output": output.output}
    except Exception as e:
        logger.error("compensation_failed", for_stage=inp.for_stage, error=str(e))
        result = {"success": False, "error": str(e)}
        raise

    activity.heartbeat({"phase": "compensation_done", "for_stage": inp.for_stage})
    await store.put(idempotency_key, result, ttl_seconds=86400)
    return result
