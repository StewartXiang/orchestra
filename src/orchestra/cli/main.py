"""orchestra CLI 入口。"""

from __future__ import annotations

import click

from .commands.validate import validate
from .commands.dry_run import dry_run
from .commands.submit import submit
from .commands.status import status
from .commands.cancel import cancel
from .commands.approve import approve
from .commands.reject import reject
from .commands.re_run import re_run
from .commands.signal import signal
from .commands.agents import agents
from .commands.schedule import schedule
from .commands.logs import logs
from .commands.replay import replay
from .commands.inspect import inspect
from .commands.chaos import chaos
from .commands.health import health
from .commands.list_pipelines import list_pipelines


@click.group()
@click.option("--host", default="localhost:7233", envvar="TEMPORAL_HOST", help="Temporal Server 地址")
@click.option("--namespace", default="default", envvar="TEMPORAL_NAMESPACE", help="Temporal Namespace")
@click.option("-o", "--output", default="table", type=click.Choice(["table", "json", "yaml"]), help="输出格式")
@click.pass_context
def main(ctx: click.Context, host: str, namespace: str, output: str) -> None:
    """orchestra — AI Agent 流水线编排引擎 CLI"""
    ctx.ensure_object(dict)
    ctx.obj["host"] = host
    ctx.obj["namespace"] = namespace
    ctx.obj["output"] = output


main.add_command(validate)
main.add_command(dry_run)
main.add_command(submit)
main.add_command(status)
main.add_command(cancel)
main.add_command(approve)
main.add_command(reject)
main.add_command(re_run)
main.add_command(signal)
main.add_command(agents)
main.add_command(schedule)
main.add_command(logs)
main.add_command(replay)
main.add_command(inspect)
main.add_command(chaos)
main.add_command(health)
main.add_command(list_pipelines, name="list")


if __name__ == "__main__":
    main()
