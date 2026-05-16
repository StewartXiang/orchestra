"""Prometheus 指标（prometheus-client 可选依赖，不可用时 no-op）。"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server
    DEFAULT_REGISTRY = CollectorRegistry(auto_describe=True)
    _PROMETHEUS_AVAILABLE = True

    pipeline_runs_total = Counter("pipeline_runs_total", "流水线提交总数", ["namespace", "name", "status"], registry=DEFAULT_REGISTRY)
    pipeline_duration_seconds = Histogram("pipeline_duration_seconds", "流水线执行时长", ["namespace", "name"], buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600), registry=DEFAULT_REGISTRY)
    pipeline_active = Gauge("pipeline_active", "当前活跃流水线数", ["namespace"], registry=DEFAULT_REGISTRY)
    pipeline_state_size_bytes = Histogram("pipeline_state_size_bytes", "Workflow State 大小（字节）", ["pipeline"], buckets=(1024, 10240, 102400, 512000, 1048576, 2097152, 10485760), registry=DEFAULT_REGISTRY)
    stage_duration_seconds = Histogram("stage_duration_seconds", "Stage 执行时长", ["pipeline", "stage", "agent"], buckets=(5, 10, 30, 60, 120, 300, 600, 1800), registry=DEFAULT_REGISTRY)
    stage_failure_total = Counter("stage_failure_total", "Stage 失败计数", ["stage", "reason"], registry=DEFAULT_REGISTRY)
    agent_status = Gauge("agent_status", "Agent 状态", ["profile", "role"], registry=DEFAULT_REGISTRY)
    agent_heartbeat_lag_seconds = Gauge("agent_heartbeat_lag_seconds", "心跳延迟（秒）", ["profile"], registry=DEFAULT_REGISTRY)
    agent_busy_slots = Gauge("agent_busy_slots", "Agent 并发占用", ["profile"], registry=DEFAULT_REGISTRY)
    agent_task_total = Counter("agent_task_total", "Agent 任务完成数", ["profile", "status"], registry=DEFAULT_REGISTRY)
    agent_task_duration_seconds = Histogram("agent_task_duration_seconds", "Agent 任务时长", ["profile"], buckets=(5, 15, 30, 60, 120, 300, 600, 1800), registry=DEFAULT_REGISTRY)
    llm_tokens_consumed_total = Counter("llm_tokens_consumed_total", "LLM Token 消耗", ["profile", "model"], registry=DEFAULT_REGISTRY)
    llm_cost_usd_total = Counter("llm_cost_usd_total", "LLM 费用（USD）", ["profile", "model"], registry=DEFAULT_REGISTRY)
    task_queue_depth = Gauge("task_queue_depth", "Task Queue 深度", ["queue"], registry=DEFAULT_REGISTRY)
    task_queue_latency_seconds = Histogram("task_queue_latency_seconds", "任务等待时间", ["queue"], buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120), registry=DEFAULT_REGISTRY)
    approval_pending_total = Gauge("approval_pending_total", "待审批节点数", ["pipeline"], registry=DEFAULT_REGISTRY)
    approval_timeout_total = Counter("approval_timeout_total", "审批超时计数", ["pipeline"], registry=DEFAULT_REGISTRY)
    temporal_worker_alive = Gauge("temporal_worker_alive", "Worker 存活数", ["profile"], registry=DEFAULT_REGISTRY)
    temporal_event_history_size = Gauge("temporal_event_history_size", "Event History 大小", ["workflow_id"], registry=DEFAULT_REGISTRY)
    pipeline_replay_failure_total = Counter("pipeline_replay_failure_total", "Replay 失败计数", [], registry=DEFAULT_REGISTRY)

except ImportError:
    _PROMETHEUS_AVAILABLE = False

    class _NoOp:
        def labels(self, **kwargs: Any) -> "_NoOp": return self
        def inc(self, amount: float = 1) -> None: pass
        def dec(self, amount: float = 1) -> None: pass
        def set(self, value: float) -> None: pass
        def observe(self, amount: float) -> None: pass

    _noop = _NoOp()
    pipeline_runs_total = stage_failure_total = agent_task_total = llm_tokens_consumed_total = _noop  # type: ignore[assignment]
    llm_cost_usd_total = approval_timeout_total = pipeline_replay_failure_total = _noop  # type: ignore[assignment]
    pipeline_duration_seconds = stage_duration_seconds = agent_task_duration_seconds = _noop  # type: ignore[assignment]
    task_queue_latency_seconds = pipeline_state_size_bytes = _noop  # type: ignore[assignment]
    pipeline_active = agent_status = agent_heartbeat_lag_seconds = agent_busy_slots = _noop  # type: ignore[assignment]
    task_queue_depth = approval_pending_total = temporal_worker_alive = temporal_event_history_size = _noop  # type: ignore[assignment]


def record_stage_failure(stage: str, reason: str) -> None:
    stage_failure_total.labels(stage=stage, reason=reason).inc()


def record_llm_usage(profile: str, model: str, tokens: int, cost_usd: float) -> None:
    llm_tokens_consumed_total.labels(profile=profile, model=model).inc(tokens)
    llm_cost_usd_total.labels(profile=profile, model=model).inc(cost_usd)


def start_metrics_server(port: int = 9100) -> None:
    if not _PROMETHEUS_AVAILABLE:
        return
    start_http_server(port, registry=DEFAULT_REGISTRY)  # type: ignore[name-defined]
