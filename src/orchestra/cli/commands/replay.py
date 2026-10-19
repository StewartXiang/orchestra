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
            from pathlib import Path

            from temporalio.client import WorkflowHistory
            from temporalio.worker import Replayer

            from orchestra.worker.registry import build_workflow_runner
            from orchestra.workflows import PipelineWorkflow

            with open(history_file) as f:
                history = WorkflowHistory.from_json(Path(history_file).stem, f.read())
            # 必须复用 Worker 的 runner，否则沙箱不一致会误报 NondeterminismError
            result = await Replayer(
                workflows=[PipelineWorkflow],
                workflow_runner=build_workflow_runner(),
            ).replay_workflow(history)
            if result.replay_failure is not None:
                raise result.replay_failure
            click_echo(f"✓ Replay passed for history: {history_file}")
        else:
            click_echo(
                f"[replay] Fetch history for {pipeline_id} and replay (requires --history-file or live Temporal)"
            )

    asyncio.run(_replay())
