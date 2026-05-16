"""orchestra validate — 静态校验 pipeline.yaml"""
from __future__ import annotations
import click
from ..output import click_echo, error, print_output

@click.command()
@click.argument("pipeline_file", type=click.Path(exists=True))
@click.pass_context
def validate(ctx: click.Context, pipeline_file: str) -> None:
    """静态校验 pipeline.yaml（JSON Schema + DAG + 引用完整性）。"""
    import yaml
    from orchestra.schema.parser import parse_pipeline
    from orchestra.schema.validator import validate_pipeline

    with open(pipeline_file) as f:
        data = yaml.safe_load(f)

    try:
        parse_pipeline(data)
    except Exception as e:
        error(str(e)); raise SystemExit(1)

    report = validate_pipeline(data)
    if report.valid:
        click_echo(f"✓ {pipeline_file} — valid")
        for w in report.warnings:
            click_echo(f"  WARN: {w}")
    else:
        click_echo(f"✗ {pipeline_file} — INVALID")
        for e_msg in report.errors:
            click_echo(f"  ERROR: {e_msg}")
        raise SystemExit(1)
