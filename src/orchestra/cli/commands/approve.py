"""orchestra approve"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.argument("stage_name")
@click.option("--as", "approver", default="cli-user")
@click.option("--reason", default="lgtm")
@click.pass_context
def approve(ctx: click.Context, pipeline_id: str, stage_name: str, approver: str, reason: str) -> None:
    """审批通过。"""
    async def _approve() -> None:
        from orchestra.cli.client import get_client
        from orchestra.workflows.signals import ApproveUpdate
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        result = await handle.execute_update("approve", ApproveUpdate(stage_name=stage_name, approver=approver, reason=reason))
        click_echo(f"✓ approved stage '{stage_name}' at {result.approved_at}")

    asyncio.run(_approve())
