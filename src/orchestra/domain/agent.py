"""Agent 规格 / Profile / Capability 类型定义。

对应 schema：
- schema/pipeline.schema.json 中 spec.agents.<name>
- schema/agent-profile.schema.json 中 profiles.<name>
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .enums import BackoffKind, HealthStatus, Role

DurationStr = Annotated[str, Field(pattern=r"^\d+(ns|us|ms|s|m|h|d|w)$")]
DnsName = Annotated[str, Field(pattern=r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")]


class ResourceQuantities(BaseModel):
    """对标 K8s ResourceRequirements。``tokensPerMinute`` 用于 LLM 令牌桶限流。"""
    model_config = ConfigDict(extra="forbid")

    memory: str | None = None  # "2Gi"
    cpu: str | None = None      # "500m" / "2"
    tokensPerMinute: int | None = Field(default=None, ge=0)


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: ResourceQuantities | None = None
    limits: ResourceQuantities | None = None


class Probe(BaseModel):
    """startup / readiness 探针。"""
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    initialDelay: DurationStr | None = None
    periodSeconds: DurationStr | None = None
    timeout: DurationStr | None = None
    failureThreshold: int | None = Field(default=None, ge=1)


class LivenessProbe(BaseModel):
    """liveness 探针：基于 Activity 心跳。"""
    model_config = ConfigDict(extra="forbid")

    heartbeatInterval: DurationStr = "15s"
    gracePeriod: DurationStr = "45s"


class RetryPolicy(BaseModel):
    """对齐 Temporal RetryPolicy。"""
    model_config = ConfigDict(extra="forbid")

    maxAttempts: int = Field(default=3, ge=1)
    backoff: BackoffKind = BackoffKind.EXPONENTIAL
    initialInterval: DurationStr = "10s"
    maxInterval: DurationStr = "5m"
    coefficient: float = Field(default=2.0, ge=1.0)
    nonRetryableErrors: list[str] = Field(default_factory=list)


class AgentSelector(BaseModel):
    """按 role + capabilities 路由到 profile。"""
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    capabilities: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    """Pipeline.spec.agents.<name> — 流水线内对 Agent 的声明。"""
    model_config = ConfigDict(extra="forbid")

    role: Role
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    taskQueue: str | None = None
    mcpEndpoint: HttpUrl | str | None = None
    maxConcurrency: int = Field(default=1, ge=1)
    resources: Resources | None = None
    startupProbe: Probe | None = None
    readinessProbe: Probe | None = None
    livenessProbe: LivenessProbe = Field(default_factory=LivenessProbe)
    retry: RetryPolicy | None = None


class Profile(BaseModel):
    """config/profiles.yaml 中的具体 Agent 实例。"""
    model_config = ConfigDict(extra="forbid")

    name: DnsName
    role: Role
    capabilities: list[str] = Field(default_factory=list)
    mcpEndpoint: str
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    taskQueue: str | None = None
    maxConcurrency: int = Field(default=1, ge=1)
    description: str | None = None


class AgentCapabilities(BaseModel):
    """Adapter ``get_capabilities()`` 返回值。"""
    model_config = ConfigDict(extra="forbid")

    role: Role
    capabilities: list[str]
    tools: list[str]
    model: str | None = None
    version: str | None = None


class AgentMetrics(BaseModel):
    """Adapter ``get_metrics()`` 返回值。"""
    model_config = ConfigDict(extra="forbid")

    busy_slots: int = 0
    queue_depth: int = 0
    last_heartbeat_age_seconds: float | None = None
    tokens_consumed_total: int = 0


class AgentHealth(BaseModel):
    """Adapter ``check_health()`` 返回值。"""
    model_config = ConfigDict(extra="forbid")

    status: HealthStatus
    message: str | None = None
    checked_at_iso: str | None = None
