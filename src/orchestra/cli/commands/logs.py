"""orchestra logs"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.option("--stage", default=None)
@click.option("--follow", "-f", is_flag=True)
@click.pass_context
def logs(ctx: click.Context, pipeline_id: str, stage: str, follow: bool) -> None:
    """查看流水线日志（Temporal Event History 摘要）。"""
    async def _logs() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        desc = await handle.describe()
        click_echo(f"Pipeline: {desc.id}  Status: {desc.status}")
        if stage:
            click_echo(f"Filtering for stage: {stage}")
        click_echo("(Full log streaming requires OTel/Loki integration)")
    asyncio.run(_logs())
