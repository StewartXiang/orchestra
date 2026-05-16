# DETERMINISM REQUIRED — see CLAUDE.md §3
"""Query 返回值数据类（只读，不修改 Workflow 状态）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProgressQuery:
    stage: str
    phase: str
    progress: float
    eta_seconds: float | None = None


@dataclass
class DagStatusQuery:
    completed: list[str] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class ApprovalStatusQuery:
    stage_name: str
    status: str        # "pending" | "approved" | "rejected" | "not_required"
    approvers: list[dict[str, Any]] = field(default_factory=list)
    timeout_at: str | None = None


@dataclass
class StateSizeQuery:
    size_bytes: int
    warning: bool = False
