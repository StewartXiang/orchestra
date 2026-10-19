# DETERMINISM REQUIRED — see CLAUDE.md §3
"""Workflow 内共用的纯工具函数（确定性、无 IO）。"""

from __future__ import annotations

import re
from datetime import timedelta


def parse_duration(d: str) -> timedelta:
    """将 Duration 字符串转为 timedelta。"""
    units = {
        "ns": 1e-9,
        "us": 1e-6,
        "ms": 1e-3,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }
    m = re.fullmatch(r"(\d+)(ns|us|ms|s|m|h|d|w)", d)
    if not m:
        return timedelta(minutes=30)  # 默认
    return timedelta(seconds=float(m.group(1)) * units[m.group(2)])
