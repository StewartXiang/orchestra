"""核心 Activity：调用 Agent 执行任务。

⚠️ 三件套铁律（CLAUDE.md §4）：
  1. 第一行 activity.heartbeat()
  2. 幂等键查询 + 写入（TTL 24h）
  3. 周期心跳 + is_cancelled() 检查
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from temporalio import activity

from ..adapters.registry import get_adapter
from ..domain.errors import OrchestraError
from ..domain.state import Checkpoint, ProgressInfo, StageOutput, TaskInput, TaskOutput
from ..observability.metrics import (
    agent_task_total,
    record_llm_usage,
    record_stage_failure,
    stage_duration_seconds,
)
from ..observability.tracing import carrier_from_traceparent, span
from ..state.idempotency import get_store


@dataclass
class AgentTaskInput:
    task: TaskInput
    stage_name: str
    pipeline_name: str


@activity.defn
async def execute_agent_task(inp: AgentTaskInput) -> StageOutput:
    """调用 Agent 执行单个 Stage 任务。

    参见 activities/README.md — 三件套铁律。
    """
    # ① 第一行心跳（报活 + started 进度）
    activity.heartbeat({"phase": "started", "stage": inp.stage_name, "progress": 0})

    info = activity.info()
    task = inp.task
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ② 幂等键查询
    idempotency_key = f"{info.workflow_id}/{info.activity_id}"
    store = get_store()
    if cached := await store.get(idempotency_key):
        return StageOutput(**cached)

    # 获取适配器
    adapter = get_adapter(task.agent_name)

    # 恢复断点（重试时从 heartbeat_details 取 checkpoint）
    heartbeat_details = info.heartbeat_details
    resume_from: Checkpoint | None = None
    if heartbeat_details:
        try:
            hb = heartbeat_details[0]
            if isinstance(hb, dict) and "checkpoint" in hb and hb["checkpoint"]:
                cp = hb["checkpoint"]
                resume_from = Checkpoint(
                    step=cp.get("step", ""),
                    progress=cp.get("progress", 0),
                    data=cp.get("data", {}),
                )
        except Exception:
            pass

    # 心跳回调（传给 adapter）
    def on_heartbeat(progress: ProgressInfo) -> None:
        activity.heartbeat({
            "phase": progress.phase,
            "stage": progress.stage,
            "progress": progress.progress,
            "eta": progress.eta_seconds,
            "checkpoint": {
                "step": progress.current_step or "",
                "progress": progress.progress,
                "data": progress.checkpoint.data if progress.checkpoint else {},
            } if progress.checkpoint or progress.current_step else None,
            "attempt": info.attempt,
        })

    # ③ 执行（含取消检查）
    parent_carrier = carrier_from_traceparent(task.traceparent)
    error_code: str | None = None
    output: TaskOutput | None = None

    with span(
        "stage.execute",
        attributes={"stage.name": inp.stage_name, "pipeline.id": info.workflow_id},
        parent_carrier=parent_carrier or None,
    ):
        try:
            output = await adapter.execute_task(
                task, on_heartbeat=on_heartbeat, resume_from=resume_from
            )
        except Exception as exc:
            error_code = type(exc).__name__
            record_stage_failure(inp.stage_name, error_code)
            agent_task_total.labels(profile=task.agent_name, status="failed").inc()
            raise

    # 记录指标
    if output:
        record_llm_usage(
            task.agent_name,
            getattr(adapter, "_role", "unknown").value if hasattr(adapter, "_role") else "unknown",
            output.tokens_consumed,
            output.cost_usd,
        )
        duration = output.duration_seconds
        stage_duration_seconds.labels(
            pipeline=inp.pipeline_name,
            stage=inp.stage_name,
            agent=task.agent_name,
        ).observe(duration)
        agent_task_total.labels(profile=task.agent_name, status="success").inc()

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = StageOutput(
        stage_name=inp.stage_name,
        success=True,
        output_path=f"$.{inp.stage_name}",  # 默认路径，Workflow 会覆盖
        output_value=output.output if output else None,
        started_at_iso=started_at,
        completed_at_iso=completed_at,
        attempts=info.attempt,
        tokens_consumed=output.tokens_consumed if output else 0,
        cost_usd=output.cost_usd if output else 0.0,
    )

    # ④ 写幂等缓存
    await store.put(idempotency_key, result.__dict__, ttl_seconds=86400)

    return result
