"""orchestra signal"""
from __future__ import annotations
import asyncio, json, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.argument("signal_name")
@click.option("--data", default="{}")
@click.pass_context
def signal(ctx: click.Context, pipeline_id: str, signal_name: str, data: str) -> None:
    """发送 Signal 到 Workflow。"""
    async def _signal() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        payload = json.loads(data)
        await handle.signal(signal_name, payload)
        click_echo(f"✓ signal '{signal_name}' sent to {pipeline_id}")
    asyncio.run(_signal())
