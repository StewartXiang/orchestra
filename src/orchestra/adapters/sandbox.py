"""工具调用白名单 + 参数 Sanitize（防 prompt 注入 / 路径穿越）。

所有工具调用（MCP tool_call）经此模块过滤后才执行。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ..domain.errors import ToolNotAllowed

# 禁止路径穿越的模式
_PATH_TRAVERSAL_RE = re.compile(r"\.\./|\.\.\\|%2e%2e|%252e%252e", re.IGNORECASE)

# Prompt 注入常见模式（标记；不拒绝，仅 warn + 添加边界标记）
_PROMPT_INJECTION_RE = re.compile(
    r"(ignore (previous|above|all) instructions?|system:\s*you are|"
    r"<\|system\|>|<system>|###\s*instruction|IGNORE INSTRUCTIONS)",
    re.IGNORECASE,
)

# Boundary marker（注入到 LLM 输入两端防止越界）
_BOUNDARY_START = "<<<ORCHESTRA_INPUT_START>>>"
_BOUNDARY_END = "<<<ORCHESTRA_INPUT_END>>>"


class Sandbox:
    """Agent 工具调用沙箱。

    Usage::

        sandbox = Sandbox(allowed_tools=["file_read", "git_commit"])
        safe_args = sandbox.check_and_sanitize("file_read", {"path": "/opt/proj/main.py"})
    """

    def __init__(self, allowed_tools: list[str], profile_name: str = "unknown") -> None:
        self._allowed = frozenset(allowed_tools)
        self._profile = profile_name

    def check_tool(self, tool_name: str) -> None:
        """校验工具名是否在白名单中。

        :raises ToolNotAllowed: 工具未授权
        """
        if tool_name not in self._allowed:
            raise ToolNotAllowed(
                f"Agent '{self._profile}' 试图调用未授权工具 '{tool_name}'，"
                f"已授权: {sorted(self._allowed)}"
            )

    def sanitize_args(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """清洗工具参数。

        - 文件路径：检测路径穿越
        - 文本内容：检测 prompt 注入并添加边界标记
        """
        result: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str):
                # 路径参数
                if key in ("path", "file_path", "filepath", "filename", "dir", "directory"):
                    value = _sanitize_path(value)
                # 文本内容（添加边界标记防止注入）
                if key in ("content", "text", "message", "prompt", "input", "task"):
                    value = _add_boundaries(value)
                result[key] = value
            elif isinstance(value, dict):
                result[key] = self.sanitize_args(tool_name, value)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize_args(tool_name, item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def check_and_sanitize(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """组合：先校验工具名，再清洗参数。"""
        self.check_tool(tool_name)
        return self.sanitize_args(tool_name, args)


def _sanitize_path(path: str) -> str:
    """拒绝路径穿越攻击。"""
    if _PATH_TRAVERSAL_RE.search(path):
        raise ToolNotAllowed(
            f"路径参数包含穿越攻击模式: {path!r}"
        )
    # 规范化（不跳出工作区）
    try:
        normalized = str(PurePosixPath(path))
        if ".." in PurePosixPath(normalized).parts:
            raise ToolNotAllowed(f"路径包含 '..': {path!r}")
    except Exception:
        pass  # 非 POSIX 路径，跳过规范化
    return path


def _add_boundaries(text: str) -> str:
    """在文本首尾添加 Orchestra 边界标记，防止 prompt 注入影响下游 Agent。"""
    return f"{_BOUNDARY_START}\n{text}\n{_BOUNDARY_END}"
