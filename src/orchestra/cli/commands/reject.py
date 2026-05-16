"""orchestra reject"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.argument("stage_name")
@click.option("--reason", required=True)
@click.option("--as", "approver", default="cli-user")
@click.pass_context
def reject(ctx: click.Context, pipeline_id: str, stage_name: str, reason: str, approver: str) -> None:
    """拒绝审批。"""
    async def _reject() -> None:
        from orchestra.cli.client import get_client
        from orchestra.workflows.signals import RejectUpdate
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        result = await handle.execute_update("reject", RejectUpdate(stage_name=stage_name, approver=approver, reason=reason))
        click_echo(f"✓ rejected stage '{stage_name}'")

    asyncio.run(_reject())
