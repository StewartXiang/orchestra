"""orchestra chaos — 故障注入"""
from __future__ import annotations
import click
from ..output import click_echo

@click.group()
def chaos() -> None:
    """故障注入（测试用）。"""

@chaos.command(name="kill-agent")
@click.argument("agent_name")
@click.option("--during", default=None, help="stage=xxx 指定在哪个 stage 期间注入")
def kill_agent(agent_name: str, during: str) -> None:
    """杀死 Agent 进程（模拟 Agent 宕机）。"""
    import subprocess
    click_echo(f"[chaos] kill agent '{agent_name}' during={during}")
    result = subprocess.run(["docker", "compose", "stop", f"worker-{agent_name}"], capture_output=True, text=True)
    click_echo(result.stdout or result.stderr)

@chaos.command(name="kill-temporal")
@click.option("--during", default=None)
def kill_temporal(during: str) -> None:
    """重启 Temporal Server。"""
    import subprocess
    click_echo("[chaos] restarting temporal-server")
    subprocess.run(["docker", "compose", "restart", "temporal-server"], capture_output=True)
    click_echo("✓ temporal-server restart requested")

@chaos.command(name="network-partition")
@click.argument("agent_name")
@click.option("--duration", default="30s")
def network_partition(agent_name: str, duration: str) -> None:
    """模拟网络分区（通过 tc/iptables，需要 root）。"""
    click_echo(f"[chaos] network-partition {agent_name} for {duration} — requires tc/iptables on host")
