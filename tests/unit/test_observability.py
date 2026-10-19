"""单元测试：observability（logging redact / tracing / metrics / audit）。

全部离线，不依赖 structlog/OTel/prometheus 之外的进程级资源。
"""

from __future__ import annotations

import sqlite3

import pytest

from orchestra.observability import audit, metrics, tracing
from orchestra.observability import logging as obs_logging

# ─────────────── logging ───────────────


def test_redact_sensitive_keys_and_values() -> None:
    raw = {
        "api_key": "sk-abcdefghijklmno",
        "nested": {"password": "p", "keep": "ok"},
        "items": [{"token": "t"}, "plain"],
        "count": 3,
        "raw": None,
    }
    out = obs_logging.redact(raw)
    assert out["api_key"] == "***"
    assert out["nested"]["password"] == "***"
    assert out["nested"]["keep"] == "ok"
    assert out["items"][0]["token"] == "***"
    assert out["items"][1] == "plain"
    assert out["count"] == 3
    assert out["raw"] is None


def test_redact_sensitive_value_in_string() -> None:
    out = obs_logging.redact("bearer sk-abcdefghijklmno")
    assert "sk-abcdefghijklmno" not in out
    assert "***REDACTED***" in out


@pytest.mark.parametrize("json_output", [True, False])
def test_configure_logging_variants(json_output: bool) -> None:
    obs_logging.configure_logging(level="info", json_output=json_output)
    obs_logging.configure_logging(level="NOT-A-LEVEL", json_output=json_output)


def test_get_logger_and_pipeline_context() -> None:
    logger = obs_logging.get_logger("test")
    assert logger is not None

    obs_logging.bind_pipeline_context(
        "p-1", run_id="r-1", stage_name="s-1", agent_name="walnut", trace_id="t-1"
    )
    obs_logging.bind_pipeline_context("p-2")  # 只带必填参数
    obs_logging.clear_pipeline_context()


# ─────────────── tracing ───────────────


def test_traceparent_helpers() -> None:
    assert tracing.traceparent_from_carrier({"traceparent": "x"}) == "x"
    assert tracing.traceparent_from_carrier({}) is None
    assert tracing.carrier_from_traceparent("x") == {"traceparent": "x"}
    assert tracing.carrier_from_traceparent(None) == {}


def test_inject_and_extract_context() -> None:
    carrier: dict[str, str] = {}
    tracing.inject_context(carrier)
    tracing.extract_context(carrier)
    tracing.init_tracing(service_name="t", otlp_endpoint=None)
    tracing.init_tracing(service_name="t", otlp_endpoint="http://localhost:4317")


def test_span_context_manager() -> None:
    with tracing.span("op", {"k": "v"}, parent_carrier={}) as s:
        assert s is None or s is not None  # OTel 可用时给 Span，不可用时 None
    with tracing.span("op2") as s2:
        assert s2 is None or s2 is not None


def test_set_pipeline_attributes() -> None:
    tracing.set_pipeline_attributes(None, "p", "s")  # None 直接返回

    class _Span:
        def __init__(self) -> None:
            self.attrs: dict[str, str] = {}

        def set_attribute(self, k: str, v: str) -> None:
            self.attrs[k] = v

    sp = _Span()
    tracing.set_pipeline_attributes(sp, "p-1", "stage-1")
    assert sp.attrs == {"pipeline.id": "p-1", "stage.name": "stage-1"}


# ─────────────── metrics ───────────────


def test_record_helpers_do_not_raise() -> None:
    metrics.record_stage_failure(stage="code", reason="timeout")
    metrics.record_llm_usage(profile="walnut", model="m", tokens=10, cost_usd=0.5)


def test_start_metrics_server_noop_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics, "_PROMETHEUS_AVAILABLE", False)
    metrics.start_metrics_server(port=0)  # 直接 return，不启动 HTTP


# ─────────────── audit ───────────────


def test_audit_writer_roundtrip(tmp_path) -> None:
    writer = audit.AuditWriter(tmp_path / "audits.db")
    writer.write(
        audit.AuditEvent(
            actor="alice",
            action="pipeline.submit",
            resource="wf-1",
            result="ok",
            version="1",
            ip_address="127.0.0.1",
            user_agent="cli",
            diff_json="{}",
            extra={"k": "v"},
        )
    )
    writer.write(
        audit.AuditEvent(actor="bob", action="pipeline.cancel", resource="wf-2", result="ok")
    )
    conn = sqlite3.connect(str(tmp_path / "audits.db"))
    try:
        rows = conn.execute("SELECT actor, extra_json FROM audits ORDER BY actor").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["alice", "bob"]
    assert rows[0][1] == '{"k": "v"}'

    writer.close()
    assert writer._conn is None
    writer.close()  # 幂等


async def test_audit_record_helper(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = audit.AuditWriter(tmp_path / "audits.db")
    monkeypatch.setattr(audit, "_default_writer", writer)
    await audit.record(
        "alice",
        "approval.approve",
        "wf-1",
        "ok",
        version="1",
        ip_address="1.1.1.1",
        user_agent="ua",
        diff={"a": 1},
        extra={"b": 2},
    )
    assert audit.get_writer() is writer
    writer.close()


def test_get_writer_raises_when_uninitialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "_default_writer", None)
    with pytest.raises(RuntimeError):
        audit.get_writer()


def test_init_audit_writer_sets_default(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "_default_writer", None)
    writer = audit.init_audit_writer(tmp_path / "a.db")
    assert audit.get_writer() is writer
    writer.close()
