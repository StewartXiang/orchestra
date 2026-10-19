"""单元测试：审批 Update 处理器（不依赖 Temporal 运行时）。

集成测试用 time-skipping 环境，无法稳定测试"发送 Update 再等结果"的交互
（见 tests/integration/test_workflow_approval.py 文件头说明），因此手动审批
路径长期无覆盖 —— 并因此藏过一个 bug：``_get_stage_approval`` 恒返回 None，
导致 ``approve`` 永远不把状态置为 approved。

这里直接调用 ``@workflow.update`` 标记的方法（装饰器不包装函数体），
用 ``patch`` 替换 ``workflow.now()`` 以脱离 Workflow 上下文。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from orchestra.workflows import pipeline_workflow as pw
from orchestra.workflows.signals import ApproveUpdate, RejectUpdate


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _pending_workflow() -> pw.PipelineWorkflow:
    wf = pw.PipelineWorkflow()
    wf._approval_state["gate"] = {"status": "pending", "approvals": []}
    return wf


def test_approve_marks_stage_approved() -> None:
    """回归：单次 approve 后状态必须变为 approved（曾经永远 pending）。"""
    wf = _pending_workflow()
    with patch.object(pw.workflow, "now", _now):
        result = asyncio.run(wf.approve(ApproveUpdate(stage_name="gate", approver="alice")))

    assert result.approver == "alice"
    assert wf._approval_state["gate"]["status"] == "approved"
    assert wf._approval_state["gate"]["approvals"][0]["approver"] == "alice"


def test_approve_unknown_stage_raises() -> None:
    wf = _pending_workflow()
    with patch.object(pw.workflow, "now", _now), pytest.raises(ValueError):
        asyncio.run(wf.approve(ApproveUpdate(stage_name="nope", approver="alice")))


def test_approve_non_pending_raises() -> None:
    wf = _pending_workflow()
    wf._approval_state["gate"]["status"] = "approved"
    with patch.object(pw.workflow, "now", _now), pytest.raises(ValueError):
        asyncio.run(wf.approve(ApproveUpdate(stage_name="gate", approver="alice")))


def test_reject_marks_stage_rejected() -> None:
    wf = _pending_workflow()
    with patch.object(pw.workflow, "now", _now):
        result = asyncio.run(
            wf.reject(RejectUpdate(stage_name="gate", approver="bob", reason="风险"))
        )

    assert result.reason == "风险"
    assert wf._approval_state["gate"]["status"] == "rejected"
    assert wf._approval_state["gate"]["reject_reason"] == "风险"


def test_check_approval_policy_requires_one_approval() -> None:
    wf = _pending_workflow()
    assert wf._check_approval_policy("gate") is False
    wf._approval_state["gate"]["approvals"].append({"approver": "alice"})
    assert wf._check_approval_policy("gate") is True
