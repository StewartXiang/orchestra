"""orchestra agents"""
from __future__ import annotations
import asyncio, click
from ..output import click_echo, print_output

@click.group()
def agents() -> None:
    """Agent 管理。"""

@agents.command(name="list")
@click.option("--label", default=None)
@click.pass_context
def list_agents(ctx: click.Context, label: str) -> None:
    """列出所有 Agent profile。"""
    from pathlib import Path
    from orchestra.adapters.registry import load_profiles_from_yaml
    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    rows = []
    for name, p in profiles.items():
        if label:
            k, _, v = label.partition("=")
            if p.labels.get(k) != v and v not in p.capabilities:
                continue
        rows.append({"name": name, "role": p.role.value, "capabilities": ",".join(p.capabilities), "endpoint": p.mcpEndpoint})
    print_output(rows, ctx.obj.get("output", "table"))

@agents.command()
@click.argument("name")
@click.pass_context
def drain(ctx: click.Context, name: str) -> None:
    """优雅下线 Agent（停止接新任务）。"""
    click_echo(f"[drain] {name} — mark NotReady in registry (not yet implemented)")

@agents.command()
@click.argument("name")
@click.pass_context
def resume(ctx: click.Context, name: str) -> None:
    """恢复 Agent 接受任务。"""
    click_echo(f"[resume] {name}")
