"""Pipeline / PipelineRun / Stage / Compensation / GlobalSpec 类型定义。

对应 schema/pipeline.schema.json 与 schema/pipeline-run.schema.json。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent import AgentSelector, AgentSpec, DnsName, DurationStr, RetryPolicy
from .enums import (
    AggregateStrategy,
    ApprovalPolicy,
    CompensationStrategy,
    NotificationChannel,
    NotificationEvent,
    OnFailure,
    OnTimeout,
    OutputStorage,
    ParentClosePolicy,
    Phase,
    Priority,
    SideEffect,
    StagePhase,
    TriggerKind,
)


# ---------- Output 配置 ----------

class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    storage: OutputStorage = OutputStorage.INLINE
    bucket: str | None = None
    ttl: DurationStr | None = None


# ---------- 子结构 ----------

class StageTimeouts(BaseModel):
    """Stage 4 字段超时，与 Temporal Activity Options 一一对应。"""
    model_config = ConfigDict(extra="forbid")

    scheduleToStart: DurationStr | None = None
    startToClose: DurationStr | None = None
    scheduleToClose: DurationStr | None = None
    heartbeat: DurationStr | None = None


class GlobalTimeouts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflowExecution: DurationStr = "24h"
    activityDefault: DurationStr = "30m"


class ChildWorkflowRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str | None = None
    parentClosePolicy: ParentClosePolicy = ParentClosePolicy.TERMINATE


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approvers: list[str] = Field(min_length=1)
    policy: ApprovalPolicy = ApprovalPolicy.ANY
    quorumCount: int | None = Field(default=None, ge=1)
    message: str | None = None
    timeout: DurationStr | None = None
    onTimeout: OnTimeout = OnTimeout.REJECT
    escalateTo: str | None = None
    reminderInterval: DurationStr | None = None
    contextFields: list[str] = Field(default_factory=list)


class DynamicStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generator: Literal["for_each", "fan_out"]
    input: str
    template: dict[str, Any]  # forward-ref to Stage（运行时校验，避免递归）
    maxParallel: int = Field(default=1, ge=1)
    maxItems: int = Field(default=1000, ge=1, le=10000)
    onItemFailure: Literal["continue", "fail_fast"] = "fail_fast"
    aggregateOutput: str | None = None


class LoopStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: list[str] = Field(min_length=1)
    condition: str
    maxIterations: int = Field(ge=1, le=100)
    onMaxReached: Literal["fail", "continue"] = "fail"


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    type: Literal["file", "directory"] = "file"
    retention: DurationStr | None = None
    compress: bool = False
    storageClass: Literal["local", "s3", "oss"] = "local"
    hash: Literal["sha256", "md5"] = "sha256"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from", description="<stage>/<artifact-name>")
    as_: str | None = Field(default=None, alias="as")


class CacheSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    ttl: DurationStr | None = None
    enabled: bool = False


# ---------- Stage ----------

class Stage(BaseModel):
    """流水线节点。``agent / agents / agentSelector / childWorkflow / approval / dynamic / loop`` 七选一。"""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: DnsName

    # 执行体（七选一）
    agent: str | None = None
    agents: list[str] | None = None
    agentSelector: AgentSelector | None = None
    childWorkflow: ChildWorkflowRef | None = None
    approval: Approval | None = None
    dynamic: DynamicStage | None = None
    loop: LoopStage | None = None

    # 调度
    dependsOn: list[str] = Field(default_factory=list)
    condition: str | None = None
    priority: int = Field(default=50, ge=0, le=100)

    # 数据
    input: str | dict[str, Any] | list[Any] | None = None
    output: str | OutputSpec | None = None
    inputSchema: dict[str, Any] | None = None
    outputSchema: dict[str, Any] | None = None
    schemaViolationPolicy: Literal["fail", "warn"] = "fail"
    requireUpstream: bool = False

    # 执行控制
    timeouts: StageTimeouts | None = None
    retry: RetryPolicy | None = None

    # 并行 / 失败语义
    aggregateStrategy: AggregateStrategy = AggregateStrategy.ALL
    quorumThreshold: float | None = Field(default=None, ge=0.0, le=1.0)
    onFailure: OnFailure = OnFailure.FAIL

    # 产出物
    artifacts: list[ArtifactSpec] = Field(default_factory=list)
    inputArtifacts: list[ArtifactRef] = Field(default_factory=list)

    # 缓存与幂等
    cache: CacheSpec | None = None
    idempotencyKey: str | None = None
    sideEffects: list[SideEffect] = Field(default_factory=list)


# ---------- Compensation ----------

class CompensationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forStage: str
    agent: str
    action: str
    runOn: Literal["any_failure", "specific_stage"] = "any_failure"


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: CompensationStrategy = CompensationStrategy.REVERSE
    maxCompensationAttempts: int = Field(default=1, ge=1)
    onCompensationFailure: Literal["alert", "abort"] = "alert"
    actions: list[CompensationAction] = Field(default_factory=list)


# ---------- Global ----------

class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[NotificationChannel] = Field(default_factory=list)
    target: str | None = None
    onEvents: list[NotificationEvent] = Field(default_factory=list)


class Retention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    historyDays: int = Field(default=30, ge=1)
    artifactsDays: int = Field(default=7, ge=1)
    successfulRunsKeep: int = Field(default=100, ge=0)
    failedRunsKeep: int = Field(default=50, ge=0)


class GlobalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeatInterval: DurationStr = "15s"
    maxConcurrency: int = Field(default=3, ge=1)
    timeouts: GlobalTimeouts = Field(default_factory=GlobalTimeouts)
    notification: Notification | None = None
    dryRun: bool = False
    artifactsBasePath: str = "/opt/agent-orchestra/artifacts"
    priority: Priority = Priority.NORMAL
    retention: Retention = Field(default_factory=Retention)


# ---------- Secret / Parameter ----------

class VaultRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    key: str


class SecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fromEnv: str | None = None
    fromFile: str | None = None
    fromVault: VaultRef | None = None


class ParameterDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "integer", "number", "boolean"]
    default: Any = None
    enum: list[Any] | None = None
    description: str | None = None
    required: bool = False


# ---------- Pipeline / PipelineRun 顶层 ----------

ApiVersion = Annotated[str, Field(pattern=r"^orchestra\.io/v\d+$")]


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DnsName
    namespace: DnsName
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class PipelineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[Stage] = Field(min_length=1)
    compensation: Compensation | None = None
    schedule: str | None = None  # cron 5 字段


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    agents: dict[str, AgentSpec]
    pipeline: PipelineBody
    secrets: list[SecretRef] = Field(default_factory=list)
    global_: GlobalSpec = Field(default_factory=GlobalSpec, alias="global")
    parameters: list[ParameterDef] = Field(default_factory=list)


class StageStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    phase: StagePhase
    attempts: int = 0
    startedAt: str | None = None
    completedAt: str | None = None
    agent: str | None = None


class PipelineStatus(BaseModel):
    """只读，由引擎维护。"""
    model_config = ConfigDict(extra="forbid")

    phase: Phase
    workflowId: str | None = None
    runId: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None
    stages: list[StageStatus] = Field(default_factory=list)


class Pipeline(BaseModel):
    """``kind: Pipeline`` 顶层资源（流水线**定义**）。"""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    apiVersion: ApiVersion
    kind: Literal["Pipeline"]
    metadata: Metadata
    spec: PipelineSpec


class PipelineRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str | None = None
    version: str | None = None


class TriggerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    actor: str | None = None
    source: str | None = None
    ref: str | None = None


class PipelineRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipelineRef: PipelineRef
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    trigger: TriggerInfo | None = None
    priority: Priority | None = None
    idempotencyKey: str | None = None


class PipelineRun(BaseModel):
    """``kind: PipelineRun`` — 一次具体执行实例。"""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    apiVersion: ApiVersion
    kind: Literal["PipelineRun"]
    metadata: Metadata
    spec: PipelineRunSpec
    status: PipelineStatus | None = None
