"""orchestra cancel"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.option("--force", is_flag=True, help="强制终止（不走 cleanup）")
@click.option("--yes", "-y", is_flag=True)
@click.pass_context
def cancel(ctx: click.Context, pipeline_id: str, force: bool, yes: bool) -> None:
    """取消流水线。"""
    if not yes:
        click.confirm(f"取消 {pipeline_id}?", abort=True)

    async def _cancel() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        if force:
            await handle.terminate(reason="force cancel via CLI")
            click_echo(f"✓ terminated: {pipeline_id}")
        else:
            await handle.cancel()
            click_echo(f"✓ cancel signal sent: {pipeline_id}")

    asyncio.run(_cancel())
