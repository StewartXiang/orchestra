"""orchestra submit — 提交流水线。"""
from __future__ import annotations
import asyncio
import uuid
import click
import yaml
from ..output import click_echo, error


@click.command()
@click.argument("pipeline_file", type=click.Path(exists=True))
@click.option("--param", "-p", multiple=True, metavar="KEY=VALUE")
@click.option("--priority", default="normal",
              type=click.Choice(["low", "normal", "high", "critical"]))
@click.option("--idempotency-key", default=None,
              help="防重提交键（幂等）")
@click.option("--dry-run", "do_dry_run", is_flag=True,
              help="校验 + 预览 DAG，不实际提交")
@click.option("--values", "values_file", default=None, type=click.Path(exists=True),
              help="values YAML 文件（参数注入）")
@click.pass_context
def submit(ctx: click.Context, pipeline_file: str, param: tuple,
           priority: str, idempotency_key: str, do_dry_run: bool,
           values_file: str) -> None:
    """提交流水线（先校验，后提交）。提交前自动运行 validate。"""
    from orchestra.schema.parser import parse_pipeline
    from orchestra.schema.validator import validate_pipeline

    data = yaml.safe_load(open(pipeline_file))

    # 注入 values 文件
    if values_file:
        vals = yaml.safe_load(open(values_file)) or {}
        click_echo(f"  注入 values: {list(vals.keys())}")
    else:
        vals = {}

    # 命令行参数覆盖
    params: dict = {**vals}
    for p in param:
        k, _, v = p.partition("=")
        params[k.strip()] = v.strip()

    # 校验
    report = validate_pipeline(data)
    if not report.valid:
        for e_msg in report.errors:
            click_echo(f"  ERROR: {e_msg}")
        raise SystemExit(1)
    for w in report.warnings:
        click_echo(f"  WARN:  {w}")

    pipeline = parse_pipeline(data)

    if do_dry_run:
        from orchestra.schema.dag import validate_dag, parallel_groups
        dag = validate_dag(pipeline)
        groups = parallel_groups(pipeline.spec.pipeline.stages)
        click_echo(f"\n[dry-run] {pipeline.metadata.name}")
        click_echo(f"  Topo: {dag.topo_order}")
        click_echo(f"  Params: {params}")
        for i, g in enumerate(groups):
            click_echo(f"  Wave {i+1}: {g}")
        return

    asyncio.run(_submit(ctx, pipeline, params, priority, idempotency_key))


async def _submit(ctx, pipeline, params: dict, priority: str, idempotency_key: str | None):
    try:
        from orchestra.worker.registry import make_client
        from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow

        client = await asyncio.wait_for(
            make_client(ctx.obj["host"], ctx.obj["namespace"]),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, Exception) as e:
        error(f"无法连接 Temporal ({ctx.obj['host']}): {e}")
        click_echo("提示: 运行 `docker compose -f deploy/docker-compose.yml up -d`")
        return

    run_id = uuid.uuid4().hex[:8]
    wf_id = idempotency_key or f"{pipeline.metadata.name}-{run_id}"
    # 首个 Agent 的 Task Queue
    first_agent = list(pipeline.spec.agents.keys())[0]
    task_queue = pipeline.spec.agents[first_agent].taskQueue or f"agent-{first_agent}"

    try:
        handle = await client.start_workflow(
            PipelineWorkflow.run,
            PipelineRunInput(
                pipeline_dict=pipeline.model_dump(by_alias=True),
                run_id=run_id,
                params=params,
            ),
            id=wf_id,
            task_queue=task_queue,
        )
        click_echo(f"✓ submitted")
        click_echo(f"  workflow_id : {handle.id}")
        click_echo(f"  run_id      : {handle.result_run_id or run_id}")
        click_echo(f"  task_queue  : {task_queue}")
        click_echo(f"  priority    : {priority}")
        click_echo(f"\n  monitor: orchestra status {handle.id}")
    except Exception as e:
        error(f"提交失败: {e}")
    finally:
        pass  # service_client has no async close in sdk>=1.7
