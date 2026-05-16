# DETERMINISM REQUIRED — see CLAUDE.md §3
"""Signal 定义：cancel / pause / resume / override（异步，无返回值）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CancelSignal:
    reason: str = ""


@dataclass
class PauseSignal:
    reason: str = ""


@dataclass
class ResumeSignal:
    pass


@dataclass
class OverrideSignal:
    key: str = ""
    value: Any = None


@dataclass
class ApproveUpdate:
    stage_name: str = ""
    approver: str = ""
    reason: str = ""


@dataclass
class RejectUpdate:
    stage_name: str = ""
    approver: str = ""
    reason: str = ""
