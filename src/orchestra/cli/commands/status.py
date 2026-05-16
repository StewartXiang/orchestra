"""orchestra status — 流水线状态查询。"""
from __future__ import annotations
import asyncio
import click
from ..output import click_echo, print_output, error


@click.command()
@click.argument("pipeline_id", required=False)
@click.option("--watch", "-w", is_flag=True, help="持续监控")
@click.option("--pending-approvals", "show_approvals", is_flag=True)
@click.option("--query", "query_name", default=None, help="执行 Workflow Query")
@click.pass_context
def status(ctx: click.Context, pipeline_id: str, watch: bool,
           show_approvals: bool, query_name: str) -> None:
    """查看流水线状态。不指定 ID 则列出最近 20 条。"""
    asyncio.run(_do_status(ctx, pipeline_id, watch, show_approvals, query_name))


async def _do_status(ctx, pipeline_id, watch, show_approvals, query_name):
    try:
        from orchestra.worker.registry import make_client
        client = await asyncio.wait_for(
            make_client(ctx.obj["host"], ctx.obj["namespace"]),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, Exception) as e:
        error(f"无法连接 Temporal ({ctx.obj['host']}): {e}")
        click_echo("提示: 运行 `docker compose -f deploy/docker-compose.yml up -d` 启动服务")
        return

    fmt = ctx.obj.get("output", "table")

    if not pipeline_id:
        # 列出最近流水线
        try:
            workflows = await client.list_workflows("ExecutionStatus='Running'")
            rows = []
            async for wf in workflows:
                rows.append({
                    "id": wf.id,
                    "run_id": wf.run_id[:8] + "...",
                    "status": str(wf.status).replace("WORKFLOW_EXECUTION_STATUS_", ""),
                    "start_time": str(wf.start_time)[:19],
                })
                if len(rows) >= 20:
                    break
            if rows:
                print_output(rows, fmt)
            else:
                click_echo("(无运行中的流水线)")
        except Exception as e:
            error(f"查询失败: {e}")
        finally:
            await client.service_client.close()
        return

    handle = client.get_workflow_handle(pipeline_id)

    async def _show():
        try:
            desc = await handle.describe()
            info = {
                "id": desc.id,
                "run_id": desc.run_id,
                "status": str(desc.status),
                "start_time": str(desc.start_time)[:19] if desc.start_time else "-",
                "close_time": str(desc.close_time)[:19] if desc.close_time else "-",
            }
            if show_approvals:
                try:
                    ap = await handle.query("get_approval_status", "")
                    info["approval_status"] = str(ap)
                except Exception:
                    info["approval_status"] = "(不适用)"
            if query_name:
                try:
                    qr = await handle.query(query_name)
                    info["query_result"] = str(qr)
                except Exception as e:
                    info["query_error"] = str(e)
            # DAG 状态
            try:
                dag = await handle.query("get_dag_status")
                info["stages_completed"] = str(getattr(dag, "completed", []))
                info["stages_running"] = str(getattr(dag, "running", []))
            except Exception:
                pass
            print_output(info, fmt)
        except Exception as e:
            error(f"获取状态失败: {e}")

    if watch:
        try:
            while True:
                await _show()
                await asyncio.sleep(3)
        except KeyboardInterrupt:
            pass
    else:
        await _show()

    await client.service_client.close()
