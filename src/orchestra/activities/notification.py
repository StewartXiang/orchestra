"""通知 Activity（飞书 Webhook / 邮件）。"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from temporalio import activity

from ..observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class NotificationInput:
    channel: str          # "feishu" / "email" / "slack"
    target: str           # 飞书 open_id / email 地址 / slack channel
    message: str
    pipeline_id: str
    event: str            # "started" / "succeeded" / "failed" / "approval_pending"
    extra: dict | None = None


@activity.defn
async def send_notification(inp: NotificationInput) -> dict:
    """发送通知（尽力而为，失败不重试）。"""
    activity.heartbeat({"phase": "notification", "channel": inp.channel})

    if inp.channel == "feishu":
        return await _send_feishu(inp)
    if inp.channel == "slack":
        return await _send_slack(inp)
    if inp.channel == "email":
        logger.info("email_notification_skipped", reason="email not implemented")
        return {"sent": False, "reason": "email not implemented"}

    return {"sent": False, "reason": f"unknown channel: {inp.channel}"}


async def _send_feishu(inp: NotificationInput) -> dict:
    """飞书卡片通知（Webhook 模式）。"""
    import os
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("feishu_webhook_not_configured")
        return {"sent": False, "reason": "FEISHU_WEBHOOK_URL not set"}

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"[{inp.event.upper()}] {inp.pipeline_id}"}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": inp.message}}],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            return {"sent": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as e:
        logger.error("feishu_send_failed", error=str(e))
        return {"sent": False, "reason": str(e)}


async def _send_slack(inp: NotificationInput) -> dict:
    import os
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return {"sent": False, "reason": "SLACK_WEBHOOK_URL not set"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json={"text": f"[{inp.event}] {inp.pipeline_id}: {inp.message}"})
            return {"sent": resp.status_code == 200}
    except Exception as e:
        return {"sent": False, "reason": str(e)}
