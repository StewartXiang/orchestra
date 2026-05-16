"""单元测试：CLI 不依赖 Temporal 的子命令（validate / dry-run / agents list）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.cli.main import main


@pytest.fixture
def runner():
    return CliRunner()


def test_validate_minimal(runner):
    result = runner.invoke(main, ["validate", "examples/minimal.pipeline.yaml"])
    assert result.exit_code == 0
    assert "valid" in result.output


def test_validate_game_dev(runner):
    result = runner.invoke(main, ["validate", "examples/game-dev.pipeline.yaml"])
    assert result.exit_code == 0
    assert "valid" in result.output


def test_validate_invalid_missing_field(runner, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("apiVersion: orchestra.io/v1\nkind: Pipeline\nmetadata:\n  name: test\n")
    result = runner.invoke(main, ["validate", str(bad)])
    assert result.exit_code != 0


def test_dry_run_minimal(runner):
    result = runner.invoke(main, ["dry-run", "examples/minimal.pipeline.yaml"])
    assert result.exit_code == 0
    assert "code" in result.output
    assert "test" in result.output


def test_dry_run_mermaid(runner):
    result = runner.invoke(main, ["dry-run", "examples/minimal.pipeline.yaml", "--output", "mermaid"])
    assert result.exit_code == 0
    assert "mermaid" in result.output
    assert "code" in result.output


def test_dry_run_dot(runner):
    result = runner.invoke(main, ["dry-run", "examples/minimal.pipeline.yaml", "--output", "dot"])
    assert result.exit_code == 0
    assert "digraph" in result.output


def test_dry_run_game_dev_waves(runner):
    result = runner.invoke(main, ["dry-run", "examples/game-dev.pipeline.yaml"])
    assert result.exit_code == 0
    assert "design-review" in result.output


def test_agents_list(runner):
    result = runner.invoke(main, ["agents", "list"])
    assert result.exit_code == 0
    # All 9 profiles should appear
    for name in ["walnut", "almond", "chestnut", "coconut", "cherry", "mango", "strawberry", "blueberry", "grape"]:
        assert name in result.output


def test_agents_list_label_filter(runner):
    result = runner.invoke(main, ["agents", "list", "--label", "capability=godot"])
    assert result.exit_code == 0
    assert "walnut" in result.output or "mango" in result.output


def test_validate_nonexistent_file(runner):
    result = runner.invoke(main, ["validate", "nonexistent.yaml"])
    assert result.exit_code != 0


def test_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "orchestra" in result.output.lower()


def test_submit_dry_run(runner):
    """submit --dry-run 不需要 Temporal，校验 + 预览 DAG。"""
    result = runner.invoke(main, [
        "submit", "examples/minimal.pipeline.yaml",
        "--dry-run",
        "--param", "task=hello world",
    ])
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    assert "minimal-pipeline" in result.output
    assert "Wave" in result.output


def test_submit_dry_run_with_values(runner, tmp_path):
    """submit --dry-run --values 支持 values 文件参数注入。"""
    values = tmp_path / "values.yaml"
    values.write_text("task: test task from values\n")
    result = runner.invoke(main, [
        "submit", "examples/minimal.pipeline.yaml",
        "--dry-run",
        "--values", str(values),
    ])
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output


def test_submit_validates_before_submit(runner, tmp_path):
    """submit 在校验失败时拒绝提交（不连接 Temporal）。"""
    bad = tmp_path / "bad.yaml"
    # 缺少 namespace 字段
    bad.write_text("""
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: bad-pipeline
spec:
  agents:
    walnut:
      role: developer
  pipeline:
    stages:
      - name: s1
        agent: nonexistent_agent
""")
    result = runner.invoke(main, ["submit", str(bad)])
    assert result.exit_code != 0


def test_health_no_mcp(runner):
    """health --no-mcp 不需要 MCP 服务在线。"""
    result = runner.invoke(main, ["health", "--no-mcp"])
    # 可能因为 Temporal 连接超时输出错误，但不应崩溃
    assert result.exit_code == 0 or "Temporal" in result.output or "无法" in result.output


def test_dry_run_with_param(runner):
    """dry-run --output mermaid + submit --dry-run --param 一致。"""
    r1 = runner.invoke(main, ["dry-run", "examples/parameterized.pipeline.yaml"])
    assert r1.exit_code == 0
    r2 = runner.invoke(main, [
        "submit", "examples/parameterized.pipeline.yaml",
        "--dry-run", "--param", "target_env=prod",
    ])
    assert r2.exit_code == 0
    assert "prod" in r2.output

