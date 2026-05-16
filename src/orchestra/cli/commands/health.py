"""orchestra health — 集群健康检查（支持离线模式）。"""
from __future__ import annotations
import asyncio
import click
from ..output import click_echo, print_output, error


@click.command()
@click.option("--agent", default=None, help="只检查指定 Agent")
@click.option("--no-mcp", is_flag=True, help="跳过 MCP 探针，只检查配置")
@click.pass_context
def health(ctx: click.Context, agent: str, no_mcp: bool) -> None:
    """集群健康检查：Temporal 连通性 + Agent MCP 存活探针。"""
    asyncio.run(_do_health(ctx, agent, no_mcp))


async def _do_health(ctx: click.Context, agent: str | None, no_mcp: bool) -> None:
    from pathlib import Path
    from orchestra.adapters.registry import load_profiles_from_yaml

    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    if agent:
        profiles = {k: v for k, v in profiles.items() if k == agent}
        if not profiles:
            error(f"未找到 profile: {agent}")
            return

    rows = []

    # 1. Temporal 连通性
    temporal_status = "unknown"
    try:
        from orchestra.worker.registry import make_client
        client = await asyncio.wait_for(
            make_client(ctx.obj["host"], ctx.obj["namespace"]),
            timeout=3.0,
        )
        await client.service_client.close()
        temporal_status = "✓ connected"
    except asyncio.TimeoutError:
        temporal_status = "✗ timeout"
    except Exception as e:
        temporal_status = f"✗ {str(e)[:50]}"

    click_echo(f"\nTemporal ({ctx.obj['host']}): {temporal_status}")
    click_echo(f"Namespace: {ctx.obj['namespace']}\n")

    # 2. Agent MCP 探针
    if no_mcp:
        for name, p in profiles.items():
            rows.append({"agent": name, "role": p.role.value,
                         "capabilities": ",".join(p.capabilities[:3]),
                         "endpoint": p.mcpEndpoint, "status": "skipped"})
    else:
        from orchestra.adapters.mcp import MCPAdapter

        async def check(name, p):
            try:
                adapter = MCPAdapter(name, p.mcpEndpoint, p.tools or [], role=p.role)
                h = await asyncio.wait_for(adapter.check_health(), timeout=3.0)
                await adapter.close()
                status = f"✓ {h.status.value}"
            except asyncio.TimeoutError:
                status = "✗ timeout"
            except Exception as e:
                status = f"✗ {type(e).__name__}"
            return {"agent": name, "role": p.role.value,
                    "capabilities": ",".join(p.capabilities[:3]),
                    "status": status}

        tasks = [check(name, p) for name, p in profiles.items()]
        rows = list(await asyncio.gather(*tasks))

    print_output(rows, ctx.obj.get("output", "table"))
