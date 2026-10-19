"""结构性守卫：Workflow 拆分的模块边界、大小与沙箱加载方式。

对应 CLAUDE.md §3（确定性沙箱）与 §5（单文件 ≤400 行）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import orchestra.workflows.pipeline_workflow as pipeline_module

WORKFLOW_DIR = Path(pipeline_module.__file__).parent

# 由 pipeline_workflow 拆分出的执行体 Mixin 模块
MIXIN_MODULES = {"control_flow", "dynamic_execution", "review_gate", "stage_execution"}

MAX_LINES = 400


def _is_passthrough(node: ast.With) -> bool:
    """判断 with 语句的上下文是否为 imports_passed_through()。"""
    return any("imports_passed_through" in ast.unparse(item.context_expr) for item in node.items)


def _modules_imported_in_passthrough(tree: ast.Module) -> set[str]:
    """收集所有位于 imports_passed_through() 块内的顶层导入模块名（去包前缀）。"""
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.With) or not _is_passthrough(node):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                found.add(sub.module.lstrip(".").split(".")[-1])
            elif isinstance(sub, ast.Import):
                for alias in sub.names:
                    found.add(alias.name.split(".")[-1])
    return found


def test_mixin_modules_are_imported_outside_passthrough() -> None:
    """执行体 Mixin 必须由沙箱加载，否则确定性拦截对它们失效。

    若放进 imports_passed_through()，方法会绑定宿主模块对象，
    Temporal 沙箱不会再重载它们，`datetime.now()` / `random.random()`
    之类的非确定性调用将无法被拦截。
    """
    tree = ast.parse(Path(pipeline_module.__file__).read_text(encoding="utf-8"))
    inside = _modules_imported_in_passthrough(tree)
    leaked = MIXIN_MODULES & inside
    assert not leaked, (
        f"这些 Mixin 在 imports_passed_through() 内导入，会绕过沙箱确定性检查: {leaked}"
    )


def test_mixin_modules_are_imported_at_top_level() -> None:
    """反向守卫：Mixin 确实在模块顶层被导入（而不是完全没被导入）。"""
    tree = ast.parse(Path(pipeline_module.__file__).read_text(encoding="utf-8"))
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.lstrip(".").split(".")[-1])
    missing = MIXIN_MODULES - top_level
    assert not missing, f"Mixin 未在 pipeline_workflow 顶层导入: {missing}"


def test_workflow_modules_within_size_limit() -> None:
    """CLAUDE.md §5：src 下单文件 ≤400 行。"""
    oversized = {
        f.name: len(f.read_text(encoding="utf-8").splitlines())
        for f in WORKFLOW_DIR.glob("*.py")
        if len(f.read_text(encoding="utf-8").splitlines()) > MAX_LINES
    }
    assert not oversized, f"超过 {MAX_LINES} 行的 workflow 文件: {oversized}"
