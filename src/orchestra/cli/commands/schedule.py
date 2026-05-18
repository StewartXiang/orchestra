"""orchestra schedule — 定时流水线调度。"""
from __future__ import annotations
import asyncio
import uuid
import click
import yaml
from ..output import click_echo, error


def _parse_cron(expr: str) -> "temporalio.common.ScheduleCalendarSpec | None":
    """解析 cron 表达式为 Temporal ScheduleSpec。"""
    from temporalio.common import ScheduleCalendarSpec, ScheduleRange, ScheduleSpec

    parts = expr.strip().split()
    if len(parts) != 5:
        return None  # caller handles invalid

    def _parse_field(val: str) -> list[ScheduleRange]:
        if val == "*":
            return [ScheduleRange(start=0)]
        if "," in val:
            return [ScheduleRange(start=int(v)) for v in val.split(",")]
        if "/" in val:
            _, step = val.split("/")
            return [ScheduleRange(start=0, step=int(step))]
        return [ScheduleRange(start=int(val))]

    return ScheduleCalendarSpec(
        second=[ScheduleRange(start=0)],
        minute=_parse_field(parts[0]),
        hour=_parse_field(parts[1]),
        day_of_month=_parse_field(parts[2]),
        month=_parse_field(parts[3]),
        day_of_week=_parse_field(parts[4]),
    )


@click.group()
def schedule() -> None:
    """Schedule 管理（定时触发流水线）。"""


@schedule.command()
@click.argument("pipeline_file", type=click.Path(exists=True))
@click.option("--cron", required=True, help="5 字段 cron 表达式（分 时 日 月 周）")
@click.option("--param", "-p", multiple=True, metavar="KEY=VALUE")
@click.pass_context
def create(ctx: click.Context, pipeline_file: str, cron: str, param: tuple) -> None:
    """创建定时 Schedule。"""
    async def _create() -> None:
        from orchestra.cli.client import get_client
        from orchestra.schema.parser import parse_pipeline
        from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
        from temporalio.common import Schedule, ScheduleActionStartWorkflow, ScheduleSpec, ScheduleState

        spec = _parse_cron(cron)
        if spec is None:
            error(f"无效 cron 表达式: {cron!r}（需要 5 字段）")
            return

        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        pipeline = parse_pipeline(pipeline_file)

        params: dict = {}
        for p in param:
            k, _, v = p.partition("=")
            params[k.strip()] = v.strip()

        run_id = uuid.uuid4().hex[:8]
        sched_id = f"sched-{pipeline.metadata.name}-{uuid.uuid4().hex[:6]}"
        first_agent = list(pipeline.spec.agents.keys())[0]
        task_queue = pipeline.spec.agents[first_agent].taskQueue or f"agent-{first_agent}"

        await client.create_schedule(
            sched_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    workflow=PipelineWorkflow.run,
                    args=[PipelineRunInput(
                        pipeline_dict=pipeline.model_dump(by_alias=True),
                        run_id=run_id,
                        params=params,
                        actor="schedule",
                    )],
                    id=f"{pipeline.metadata.name}-{run_id}",
                    task_queue=task_queue,
                ),
                spec=ScheduleSpec(calendars=[spec]),
                state=ScheduleState(note=f"Pipeline: {pipeline.metadata.name}"),
            ),
        )
        click_echo(f"✓ schedule created")
        click_echo(f"  id   : {sched_id}")
        click_echo(f"  cron : {cron}")
        click_echo(f"  queue: {task_queue}")

    asyncio.run(_create())


@schedule.command(name="list")
@click.pass_context
def list_schedules(ctx: click.Context) -> None:
    """列出所有 Schedule。"""
    async def _list() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        async for s in client.list_schedules():
            click_echo(f"  {s.id}  {s.schedule.state.note or ''}")
    asyncio.run(_list())


@schedule.command()
@click.argument("schedule_id")
@click.pass_context
def pause(ctx: click.Context, schedule_id: str) -> None:
    """暂停 Schedule。"""
    async def _pause() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_schedule_handle(schedule_id)
        await handle.pause(note="Paused via CLI")
        click_echo(f"✓ schedule paused: {schedule_id}")
    asyncio.run(_pause())


@schedule.command()
@click.argument("schedule_id")
@click.pass_context
def resume(ctx: click.Context, schedule_id: str) -> None:
    """恢复 Schedule。"""
    async def _resume() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_schedule_handle(schedule_id)
        await handle.resume(note="Resumed via CLI")
        click_echo(f"✓ schedule resumed: {schedule_id}")
    asyncio.run(_resume())


@schedule.command()
@click.argument("schedule_id")
@click.pass_context
def trigger(ctx: click.Context, schedule_id: str) -> None:
    """立即手动触发一次 Schedule。"""
    async def _trigger() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_schedule_handle(schedule_id)
        await handle.trigger()
        click_echo(f"✓ schedule triggered: {schedule_id}")
    asyncio.run(_trigger())


@schedule.command()
@click.argument("schedule_id")
@click.pass_context
def delete(ctx: click.Context, schedule_id: str) -> None:
    """删除 Schedule。"""
    async def _delete() -> None:
        from orchestra.cli.client import get_client
        client = await get_client(ctx.obj["host"], ctx.obj["namespace"])
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        click_echo(f"✓ schedule deleted: {schedule_id}")
    asyncio.run(_delete())
