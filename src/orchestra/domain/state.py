"""WorkflowState / Stage 输入输出 / Artifact 引用 / Checkpoint / 进度 类型定义。

State 是 Workflow 内全局可读、按 Stage output.path 写隔离的 JSON 树。
Activity 不直接持有 State —— 由 Workflow 提供 input subset，由 Activity 返回 output 后由 Workflow 合并。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactReference(BaseModel):
    """大对象在 State 中的指针（output.storage = reference / oss）。"""
    model_config = ConfigDict(extra="forbid")

    path: str               # 本地路径或 OSS URL
    sha256: str
    size: int = Field(ge=0)
    storage: str            # "local" / "oss" / "s3"
    bucket: str | None = None
    ttl_seconds: int | None = None


class Checkpoint(BaseModel):
    """长 Activity 心跳中携带的检查点，用于失败重试时断点续传。"""
    model_config = ConfigDict(extra="forbid")

    step: str               # 业务步骤标识（如 "compiling" / "tested" / "ui_verified"）
    progress: float = Field(ge=0.0, le=100.0)
    data: dict[str, Any] = Field(default_factory=dict)
    written_at_iso: str | None = None


class ProgressInfo(BaseModel):
    """Activity 心跳载荷。供 CLI ``status --watch`` 显示。"""
    model_config = ConfigDict(extra="forbid")

    stage: str
    phase: str              # "started" / "running" / "completing"
    progress: float = Field(ge=0.0, le=100.0)
    eta_seconds: float | None = None
    current_step: str | None = None
    checkpoint: Checkpoint | None = None
    attempt: int = 1


class WorkflowState(BaseModel):
    """Workflow 全局 State。

    根字段约定：
    - ``params``: 运行时参数（只读，由 PipelineRun.spec.parameters 注入）
    - ``<stage_name>``: 各 Stage 的输出（由 ``output.path`` 决定具体子路径）

    Stage 仅能写自己 ``output.path`` 指向的子树；引擎在 Stage 完成时校验。
    """
    model_config = ConfigDict(extra="allow")

    params: dict[str, Any] = Field(default_factory=dict)


class StageOutput(BaseModel):
    """Activity 返回给 Workflow 的标准包装。"""
    model_config = ConfigDict(extra="forbid")

    stage_name: str
    success: bool
    output_path: str        # JSONPath，如 "$.code.patch"
    output_value: Any | ArtifactReference | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at_iso: str
    completed_at_iso: str
    attempts: int = 1
    tokens_consumed: int = 0
    cost_usd: float = 0.0


class TaskInput(BaseModel):
    """Adapter ``execute_task`` 入参：Activity 派发给 Agent 时的载荷。"""
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    stage_name: str
    agent_name: str
    role: str
    tools: list[str]
    input: Any                            # 由 Workflow 按 stage.input JSONPath 提取
    idempotency_key: str
    deadline_iso: str | None = None
    traceparent: str | None = None        # OTel 上下文传播


class TaskOutput(BaseModel):
    """Adapter ``execute_task`` 返回值。"""
    model_config = ConfigDict(extra="forbid")

    output: Any                           # 由 Workflow 按 stage.output 写入 State
    tokens_consumed: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
