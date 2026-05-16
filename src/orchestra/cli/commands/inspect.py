"""orchestra inspect — 导出 Event History"""
from __future__ import annotations
import asyncio, json, click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.option("--download-history", "out_file", default=None, type=click.Path())
@click.pass_context
def inspect(ctx: click.Context, pipeline_id: str, out_file: str) -> None:
    """导出 Workflow Event History（JSON）。"""
    async def _inspect() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_workflow_handle(pipeline_id)
        desc = await handle.describe()
        info = {"id": desc.id, "run_id": desc.run_id, "status": str(desc.status)}
        if out_file:
            with open(out_file, "w") as f:
                json.dump(info, f, indent=2)
            click_echo(f"✓ history saved to {out_file}")
        else:
            click_echo(json.dumps(info, indent=2))
    asyncio.run(_inspect())
