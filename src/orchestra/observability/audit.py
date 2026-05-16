"""审计日志 Schema + Writer。

审计日志独立于 Temporal Event History 存储（Event History 受 retention 约束，审计保留 ≥1 年）。
后端：SQLite（默认单机）或 PostgreSQL（规模化）。

动作词表（来自 design.md）：
  pipeline.{submit, cancel, re-run}
  approval.{approve, reject}
  signal.<name>
  schedule.{create, pause, resume, delete, trigger}
  config.update
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AuditEvent:
    """审计事件（来自 design.md "审计设计"）。"""
    actor: str
    action: str
    resource: str
    result: str
    version: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    diff_json: str | None = None
    audit_id: str = field(default_factory=lambda: f"audit-{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS audits (
    audit_id    TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    version     TEXT,
    result      TEXT,
    ip_address  TEXT,
    user_agent  TEXT,
    diff_json   TEXT,
    extra_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_actor_ts    ON audits(actor, ts);
CREATE INDEX IF NOT EXISTS idx_resource_ts ON audits(resource, ts);
CREATE INDEX IF NOT EXISTS idx_action_ts   ON audits(action, ts);
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO audits
    (audit_id, ts, actor, action, resource, version, result, ip_address, user_agent, diff_json, extra_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class AuditWriter:
    """线程安全的审计日志写入器（SQLite 后端）。"""

    def __init__(self, db_path: str | Path = "audits.db") -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.executescript(_CREATE_SQL)
            self._conn.commit()
        return self._conn

    def write(self, event: AuditEvent) -> None:
        conn = self._get_conn()
        conn.execute(_INSERT_SQL, (
            event.audit_id,
            event.timestamp,
            event.actor,
            event.action,
            event.resource,
            event.version,
            event.result,
            event.ip_address,
            event.user_agent,
            event.diff_json,
            json.dumps(event.extra) if event.extra else None,
        ))
        conn.commit()

    async def write_async(self, event: AuditEvent) -> None:
        """异步写（在后台线程执行，不阻塞事件循环）。"""
        await asyncio.get_running_loop().run_in_executor(None, self.write, event)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# 模块级默认 writer（Worker 进程中通过 init_audit_writer 替换）
_default_writer: AuditWriter | None = None


def init_audit_writer(db_path: str | Path) -> AuditWriter:
    """初始化并设置模块级默认 writer。"""
    global _default_writer
    _default_writer = AuditWriter(db_path)
    return _default_writer


def get_writer() -> AuditWriter:
    if _default_writer is None:
        raise RuntimeError("AuditWriter 未初始化，请先调用 init_audit_writer()")
    return _default_writer


async def record(
    actor: str,
    action: str,
    resource: str,
    result: str,
    *,
    version: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    diff: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """便捷函数：异步写一条审计事件。"""
    event = AuditEvent(
        actor=actor,
        action=action,
        resource=resource,
        result=result,
        version=version,
        ip_address=ip_address,
        user_agent=user_agent,
        diff_json=json.dumps(diff) if diff else None,
        extra=extra or {},
    )
    await get_writer().write_async(event)
