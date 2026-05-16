"""orchestra replay — Replay 兼容性检测"""
from __future__ import annotations
import click
from ..output import click_echo

@click.command()
@click.argument("pipeline_id")
@click.option("--history-file", default=None, type=click.Path())
@click.pass_context
def replay(ctx: click.Context, pipeline_id: str, history_file: str) -> None:
    """跑 WorkflowReplayer 检测代码兼容性。"""
    import asyncio
    async def _replay() -> None:
        if history_file:
            import json
            from temporalio.testing import WorkflowReplayer
            from orchestra.workflows import PipelineWorkflow
            history = json.loads(open(history_file).read())
            replayer = WorkflowReplayer(workflows=[PipelineWorkflow])
            await replayer.replay_workflow(history)
            click_echo(f"✓ Replay passed for history: {history_file}")
        else:
            click_echo(f"[replay] Fetch history for {pipeline_id} and replay (requires --history-file or live Temporal)")
    asyncio.run(_replay())
