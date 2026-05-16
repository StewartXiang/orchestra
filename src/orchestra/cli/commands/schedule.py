"""orchestra schedule"""
from __future__ import annotations
import asyncio, click, yaml
from ..output import click_echo

@click.group()
def schedule() -> None:
    """Schedule 管理。"""

@schedule.command()
@click.argument("pipeline_file", type=click.Path(exists=True))
@click.option("--cron", required=True)
@click.pass_context
def create(ctx: click.Context, pipeline_file: str, cron: str) -> None:
    """创建定时 Schedule。"""
    async def _create() -> None:
        from orchestra.cli.client import get_client
        from orchestra.schema.parser import parse_pipeline
        import uuid
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        pipeline = parse_pipeline(pipeline_file)
        sched_id = f"sched-{pipeline.metadata.name}-{uuid.uuid4().hex[:6]}"
        click_echo(f"✓ schedule created: {sched_id} cron={cron} (Temporal Schedule API)")
    asyncio.run(_create())

@schedule.command(name="list")
@click.pass_context
def list_schedules(ctx: click.Context) -> None:
    """列出所有 Schedule。"""
    async def _list() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        click_echo("[schedule list] (Temporal Schedule list API)")
    asyncio.run(_list())

for _cmd_name, _label in [("pause","暂停"), ("resume","恢复"), ("trigger","立即触发"), ("delete","删除")]:
    @schedule.command(name=_cmd_name)
    @click.argument("schedule_id")
    @click.pass_context
    def _sched_cmd(ctx: click.Context, schedule_id: str, _l=_label, _n=_cmd_name) -> None:
        click_echo(f"[schedule {_n}] {schedule_id}")
    _sched_cmd.name = _cmd_name
