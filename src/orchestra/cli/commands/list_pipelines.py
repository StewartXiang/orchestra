"""orchestra list"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo, print_output

@click.command()
@click.option("--status", "filter_status", default=None)
@click.option("--since", default="24h")
@click.option("--limit", default=50)
@click.option("--label", default=None)
@click.pass_context
def list_pipelines(ctx: click.Context, filter_status: str, since: str, limit: int, label: str) -> None:
    """列出流水线（默认 24h 内）。"""
    async def _list() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        click_echo(f"[list] since={since} status={filter_status} limit={limit} (Temporal Visibility API)")
    asyncio.run(_list())
