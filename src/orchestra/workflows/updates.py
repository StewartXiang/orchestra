# DETERMINISM REQUIRED — see CLAUDE.md §3
"""Update 返回值数据类（同步，带返回值 + 校验）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApproveResult:
    approved_at: str
    approver: str


@dataclass
class RejectResult:
    rejected_at: str
    approver: str
    reason: str
