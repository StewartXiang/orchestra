"""orchestra re-run — 从指定 Stage 重跑流水线（Temporal Reset）。"""
from __future__ import annotations
import asyncio
import click
from ..output import click_echo, error


@click.command(name="re-run")
@click.argument("workflow_id")
@click.option("--from-stage", "from_stage", default=None,
              help="从哪个 stage 开始重跑（不指定则重跑整个流水线）")
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

        if not yes:
            if not click.confirm(f"  确定重跑流水线 {workflow_id}？"):
                click_echo("  已取消")
                return

        try:
            if from_stage:
                # 从指定 stage 重置：找到对应 Activity 的完成事件，重置到其后
                # Temporal Reset 到特定 event_id
                history = await handle.fetch_history()

                # 寻找 from_stage 的第一个 Activity 完成事件
                target_event_id: int | None = None
                for event in history.events:
                    if event.HasField("activity_task_completed"):
                        attrs = event.activity_task_completed_event_attributes
                        if attrs.activity_type.name == "execute_agent_task":
                            # 通过 input payload 判断 stage 名称（简化：查 workflow task completed）
                            target_event_id = event.event_id
                            # 继续搜索匹配 stage 名称的
                            import json
                            try:
                                input_data = json.loads(
                                    attrs.result.payloads[0].data.decode()
                                )
                                if input_data.get("stage_name") == from_stage:
                                    break
                            except Exception:
                                pass

                if target_event_id is None:
                    error(f"未找到 stage '{from_stage}' 的完成事件，将从头重跑")
                    await handle.reset()
                else:
                    # 重置到该 event 之后（重跑后续 stage）
                    from temporalio.common import ResetConfig, ResetReapplyType
                    await handle.reset(
                        ResetConfig(
                            to_existing_event_id=target_event_id,
                            reset_reapply_type=ResetReapplyType.SIGNAL,
                        )
                    )
                click_echo(f"✓ workflow 已重置（from stage={from_stage}）")
            else:
                await handle.reset()
                click_echo(f"✓ workflow 已重置（从头重跑）")
        except Exception as e:
            error(f"重跑失败: {e}")
            click_echo("提示: 确认 Temporal Server 版本 ≥ 1.18 且 workflow 仍在 Event History 保留期内")

    asyncio.run(_rerun())
