"""orchestra dry-run — 预览 DAG 拓扑"""
from __future__ import annotations
import click
from ..output import click_echo, print_output

@click.command(name="dry-run")
@click.argument("pipeline_file", type=click.Path(exists=True))
@click.option("--output", "fmt", default="text", type=click.Choice(["text", "dot", "mermaid"]))
@click.pass_context
def dry_run(ctx: click.Context, pipeline_file: str, fmt: str) -> None:
    """预览 DAG 拓扑（不执行）。"""
    import yaml
    from orchestra.schema.parser import parse_pipeline
    from orchestra.schema.dag import validate_dag, parallel_groups

    data = yaml.safe_load(open(pipeline_file))
    pipeline = parse_pipeline(data)
    stages = pipeline.spec.pipeline.stages
    dag = validate_dag(pipeline)

    if not dag.valid:
        for e in dag.errors:
            click_echo(f"  ERROR: {e}")
        raise SystemExit(1)

    groups = parallel_groups(stages)
    if fmt == "mermaid":
        lines = ["```mermaid", "graph LR"]
        for s in stages:
            for dep in s.dependsOn:
                lines.append(f"  {dep} --> {s.name}")
        lines.append("```")
        click_echo("\n".join(lines))
    elif fmt == "dot":
        lines = ["digraph G {"]
        for s in stages:
            for dep in s.dependsOn:
                lines.append(f'  "{dep}" -> "{s.name}";')
        lines.append("}")
        click_echo("\n".join(lines))
    else:
        click_echo(f"Pipeline: {pipeline.metadata.name}")
        click_echo(f"Topo order: {dag.topo_order}")
        click_echo("Parallel groups:")
        for i, group in enumerate(groups):
            click_echo(f"  Wave {i+1}: {group}")
