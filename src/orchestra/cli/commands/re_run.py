"""orchestra re-run"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo

@click.command(name="re-run")
@click.argument("pipeline_id")
@click.option("--from", "from_stage", default=None)
@click.pass_context
def re_run(ctx: click.Context, pipeline_id: str, from_stage: str) -> None:
    """重跑流水线（Temporal Reset）。"""
    async def _rerun() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        click_echo(f"[re-run] {pipeline_id} from_stage={from_stage or 'beginning'} (Temporal Reset — requires Temporal SDK reset API)")
    asyncio.run(_rerun())
