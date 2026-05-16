"""审计日志写入 Activity（对 observability.audit 的 Activity 包装）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from temporalio import activity

from ..observability.audit import AuditEvent, get_writer
from ..observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AuditInput:
    actor: str
    action: str
    resource: str
    result: str
    version: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    diff: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None


@activity.defn
async def write_audit_log(inp: AuditInput) -> None:
    """写入审计日志（尽力而为，不阻塞流水线主流程）。"""
    import json
    activity.heartbeat({"phase": "audit", "action": inp.action})

    event = AuditEvent(
        actor=inp.actor,
        action=inp.action,
        resource=inp.resource,
        result=inp.result,
        version=inp.version,
        ip_address=inp.ip_address,
        user_agent=inp.user_agent,
        diff_json=json.dumps(inp.diff) if inp.diff else None,
        extra=inp.extra or {},
    )
    try:
        get_writer().write(event)
        logger.info("audit_written", action=inp.action, resource=inp.resource)
    except Exception as e:
        logger.error("audit_write_failed", error=str(e))
        # 审计日志失败不影响流水线，不 raise
