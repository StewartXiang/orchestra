"""orchestra init — 交互式生成 Pipeline + Profiles 配置。"""
from __future__ import annotations

import click
import yaml

from ..output import click_echo


@click.command()
@click.option("--out", "-o", default=".", type=click.Path(file_okay=False),
              help="输出目录（默认当前目录）")
@click.option("--non-interactive", is_flag=True, help="非交互模式（使用默认值）")
def init(out: str, non_interactive: bool) -> None:
    """交互式初始化项目配置。

    生成文件：
      - {name}.pipeline.yaml   流水线定义
      - values.yaml            参数配置
      - README.md              项目说明（可选）
    """
    click_echo("")
    click_echo("  🎻 Orchestra 项目初始化")
    click_echo("  ──────────────────────")
    click_echo("")

    if non_interactive:
        name = "my-pipeline"
        description = "My first Orchestra pipeline"
        agents = ["agent-1"]
        stages_input = "code,test"
    else:
        name = click.prompt("  流水线名称", default="my-pipeline").strip()
        description = click.prompt("  描述", default="My first Orchestra pipeline").strip()
        click.echo("")
        click.echo("  Agent 配置（输入名称，空格分隔，至少 1 个）")
        click.echo("  示例: dev-agent test-agent deploy-agent")
        agents_raw = click.prompt("  Agent 名称", default="dev-agent").strip()
        agents = [a.strip() for a in agents_raw.split() if a.strip()]
        click.echo("")
        click.echo("  Stage 配置（输入名称，逗号分隔，至少 2 个）")
        click.echo("  示例: code,test,deploy")
        stages_raw = click.prompt("  Stage 名称", default="code,test").strip()
        stages_input = stages_raw

    stage_names = [s.strip() for s in stages_input.split(",") if s.strip()]
    if len(stage_names) < 1:
        stage_names = ["code", "test"]
    if len(agents) < 1:
        agents = ["agent-1"]

    # ── 生成 pipeline YAML ──
    pipeline: dict = {
        "apiVersion": "orchestra.io/v1",
        "kind": "Pipeline",
        "metadata": {
            "name": name,
            "namespace": "default",
            "version": "0.1.0",
            "labels": {"env": "dev"},
        },
        "spec": {
            "description": description,
            "agents": {},
            "pipeline": {"stages": []},
            "parameters": [
                {"name": "task", "type": "string", "required": True,
                 "description": "任务描述"},
            ],
            "global": {
                "heartbeatInterval": "15s",
                "timeouts": {"workflowExecution": "1h"},
            },
        },
    }

    # Agent 配置
    for i, agent_name in enumerate(agents):
        port = 18961 + i
        pipeline["spec"]["agents"][agent_name] = {
            "role": "developer",
            "capabilities": ["python", "git"],
            "mcpEndpoint": f"mcp://localhost:{port}",
            "tools": ["file_read", "file_write", "shell_run"],
            "livenessProbe": {"heartbeatInterval": "15s", "gracePeriod": "45s"},
        }

    # Stage 配置（线性链）
    for i, stage_name in enumerate(stage_names):
        stage: dict = {
            "name": stage_name,
            "agent": agents[min(i, len(agents) - 1)],
            "output": f"$.{stage_name}",
            "timeouts": {"startToClose": "10m", "heartbeat": "30s"},
        }
        if i > 0:
            stage["dependsOn"] = [stage_names[i - 1]]
        if i == 0:
            stage["input"] = "$.params.task"
        pipeline["spec"]["pipeline"]["stages"].append(stage)

    # ── 写入文件 ──
    import os
    out_dir = os.path.abspath(out)
    os.makedirs(out_dir, exist_ok=True)

    pipeline_path = os.path.join(out_dir, f"{name}.pipeline.yaml")
    with open(pipeline_path, "w") as f:
        yaml.dump(pipeline, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # values.yaml
    values: dict = {"task": "你的第一个任务描述"}
    values_path = os.path.join(out_dir, "values.yaml")
    with open(values_path, "w") as f:
        yaml.dump(values, f, allow_unicode=True)

    click_echo("")
    click_echo(f"  ✓ 已生成 {pipeline_path}")
    click_echo(f"  ✓ 已生成 {values_path}")
    click_echo("")
    click_echo("  下一步:")
    click_echo(f"    1. orchestra validate {pipeline_path}")
    click_echo(f"    2. orchestra dry-run {pipeline_path}")
    click_echo(f"    3. orchestra submit {pipeline_path} --values values.yaml")
    click_echo("")
    click_echo("  提示: 确保 Agent 在 MCP endpoint 可访问。")
    click_echo("  如果没有 Agent，使用内置 demo:")
    click_echo("    docker compose -f deploy/docker-compose.demo.yml up -d")
    click_echo("    orchestra submit examples/minimal-demo.pipeline.yaml --param task='hello'")
    click_echo("")
