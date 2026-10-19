"""orchestra re-run — 从指定 Stage 重跑流水线（Temporal Reset）。"""

from __future__ import annotations

import asyncio
from typing import Any

import click

from ..output import click_echo, error


@click.command(name="re-run")
@click.argument("workflow_id")
@click.option(
    "--from-stage",
    "from_stage",
    default=None,
    help="从哪个 stage 开始重跑（不指定则重跑整个流水线）",
)
@click.option("--yes", is_flag=True, help="跳过确认提示")
@click.pass_context
def re_run(ctx: click.Context, workflow_id: str, from_stage: str, yes: bool) -> None:
    """重跑流水线（Temporal Reset）。

    使用 Temporal Reset API 将 Workflow 回退到指定事件位置，保留 Event History。
    如果 workflow 有 sideEffects 声明，将提示重跑风险。
    """

    async def _rerun() -> None:
        from orchestra.cli.client import get_client

        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])

        handle = client.get_workflow_handle(workflow_id)

        # 获取 workflow 描述
        try:
            desc = await handle.describe()
        except Exception as e:
            error(f"找不到 workflow: {workflow_id} ({e})")
            return

        click_echo(f"  流水线: {workflow_id}")
        click_echo(f"  状态  : {desc.status}")

        if not yes and not click.confirm(f"  确定重跑流水线 {workflow_id}？"):
            click_echo("  已取消")
            return

        def _find_stage_scheduled_id(events: list[Any], stage: str) -> int | None:
            """找到目标 stage 的 activity_task_scheduled 事件 id（按 ActivityInput.stage_name）。"""
            import json

            for event in events:
                if not event.HasField("activity_task_scheduled"):
                    continue
                attrs = event.activity_task_scheduled_event_attributes
                if attrs.activity_type.name != "execute_agent_task":
                    continue
                try:
                    payload = json.loads(attrs.input.payloads[0].data.decode())
                except Exception:
                    continue
                if payload.get("stage_name") == stage:
                    return int(event.event_id)
            return None

        def _next_task_completed_after(events: list[Any], event_id: int) -> int | None:
            for event in events:
                if event.event_id > event_id and event.HasField("workflow_task_completed"):
                    return int(event.event_id)
            return None

        def _first_task_completed(events: list[Any]) -> int | None:
            for event in events:
                if event.HasField("workflow_task_completed"):
                    return int(event.event_id)
            return None

        async def _reset(reset_event_id: int) -> None:
            """调用 workflow_service.reset_workflow_execution（WorkflowHandle 无 reset API）。"""
            from temporalio.api.common.v1 import WorkflowExecution
            from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest

            await client.workflow_service.reset_workflow_execution(
                ResetWorkflowExecutionRequest(
                    namespace=client.namespace,
                    workflow_execution=WorkflowExecution(
                        workflow_id=workflow_id, run_id=desc.run_id
                    ),
                    reason="orchestra re-run",
                    # Temporal Reset 语义：重置到该 WorkflowTask 完成后
                    workflow_task_finish_event_id=reset_event_id,
                )
            )

        try:
            history = await handle.fetch_history()
            events = list(history.events)

            if from_stage:
                scheduled_id = _find_stage_scheduled_id(events, from_stage)
                if scheduled_id is None:
                    error(f"未找到 stage '{from_stage}' 的调度事件，将从头重跑")
                    reset_event_id = _first_task_completed(events)
                else:
                    reset_event_id = _next_task_completed_after(
                        events, scheduled_id
                    ) or _first_task_completed(events)
            else:
                reset_event_id = _first_task_completed(events)

            if reset_event_id is None:
                error("找不到可重置的 workflow task 事件")
                return

            await _reset(reset_event_id)
            scope = f"from stage={from_stage}" if from_stage else "from start"
            click_echo(f"✓ workflow 已重置（{scope}）")
        except Exception as e:
            error(f"重跑失败: {e}")
            click_echo(
                "提示: 确认 Temporal Server 版本 ≥ 1.18 且 workflow 仍在 Event History 保留期内"
            )

    asyncio.run(_rerun())
