"""多格式输出（json / yaml / table）。"""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    _RICH = True
except ImportError:
    _RICH = False

try:
    import yaml as _yaml
    _YAML = True
except ImportError:
    _YAML = False

console = Console() if _RICH else None  # type: ignore[assignment]


def print_json(data: Any) -> None:
    click_echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def print_yaml(data: Any) -> None:
    if _YAML:
        click_echo(_yaml.dump(data, allow_unicode=True, default_flow_style=False))
    else:
        print_json(data)


def print_table(rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    if not rows:
        click_echo("(no results)")
        return
    if headers is None:
        headers = list(rows[0].keys())

    if _RICH and console:
        table = Table(*headers, show_lines=False)
        for row in rows:
            table.add_row(*[str(row.get(h, "")) for h in headers])
        console.print(table)
    else:
        # Fallback: simple aligned output
        widths = {h: len(h) for h in headers}
        for row in rows:
            for h in headers:
                widths[h] = max(widths[h], len(str(row.get(h, ""))))
        header_line = "  ".join(h.ljust(widths[h]) for h in headers)
        click_echo(header_line)
        click_echo("-" * len(header_line))
        for row in rows:
            click_echo("  ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))


def print_output(data: Any, fmt: str = "table") -> None:
    if fmt == "json":
        print_json(data)
    elif fmt == "yaml":
        print_yaml(data)
    elif isinstance(data, list):
        print_table(data)
    else:
        print_json(data)


def click_echo(msg: str) -> None:
    try:
        import click
        click.echo(msg)
    except ImportError:
        print(msg)


def error(msg: str) -> None:
    try:
        import click
        click.secho(f"Error: {msg}", fg="red", err=True)
    except ImportError:
        print(f"Error: {msg}", file=sys.stderr)
