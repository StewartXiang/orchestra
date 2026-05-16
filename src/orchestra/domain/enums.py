"""Orchestra 全部枚举集中地。

新增枚举值时同步更新：
- schema/pipeline.schema.json 中的 enum 字段
- docs/design.md 中的对应文字描述
- examples/*.yaml 至少一个示例覆盖
"""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    """PipelineRun 生命周期阶段。"""
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED = "Paused"
    PENDING_APPROVAL = "PendingApproval"
    CANCELLING = "Cancelling"
    COMPENSATING = "Compensating"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class StagePhase(str, Enum):
    """单个 Stage 的阶段。"""
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"


class Role(str, Enum):
    """Agent 逻辑角色。新增需先在 config/capabilities.yaml roles: 词表声明。"""
    DEVELOPER = "developer"
    TESTER = "tester"
    DESIGNER = "designer"
    CI_ENGINEER = "ci_engineer"
    CHAT = "chat"
    STANDBY = "standby"


class Priority(str, Enum):
    """流水线 / Stage 优先级。"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class BackoffKind(str, Enum):
    """重试退避算法。"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class AggregateStrategy(str, Enum):
    """并行 Stage 结果聚合策略。详见 design.md "并行执行与聚合"。"""
    ALL = "all"
    ANY = "any"
    FIRST = "first"
    MERGE = "merge"
    VOTE = "vote"
    QUORUM = "quorum"


class OnFailure(str, Enum):
    """Stage 失败时的下一步动作。"""
    CONTINUE = "continue"
    FAIL = "fail"
    COMPENSATE = "compensate"


class OutputStorage(str, Enum):
    """Stage 输出存储策略。"""
    INLINE = "inline"
    REFERENCE = "reference"
    OSS = "oss"


class StorageClass(str, Enum):
    """Artifact 存储后端。"""
    LOCAL = "local"
    S3 = "s3"
    OSS = "oss"


class CompensationStrategy(str, Enum):
    REVERSE = "reverse"
    PARALLEL = "parallel"
    CUSTOM = "custom"


class ApprovalPolicy(str, Enum):
    ANY = "any"
    ALL = "all"
    QUORUM = "quorum"


class OnTimeout(str, Enum):
    REJECT = "reject"
    APPROVE = "approve"
    ESCALATE = "escalate"


class ParentClosePolicy(str, Enum):
    """子 Workflow 在父结束时的行为。"""
    TERMINATE = "TERMINATE"
    ABANDON = "ABANDON"
    REQUEST_CANCEL = "REQUEST_CANCEL"


class SideEffect(str, Enum):
    """Stage 声明的副作用类别，影响重跑风险评估。"""
    GIT = "git"
    FS = "fs"
    DEPLOY = "deploy"
    NETWORK = "network"
    DB = "db"


class TriggerKind(str, Enum):
    """PipelineRun 触发方式。"""
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    SIGNAL = "signal"
    API = "api"


class NotificationChannel(str, Enum):
    FEISHU = "feishu"
    EMAIL = "email"
    SLACK = "slack"
    DINGTALK = "dingtalk"


class NotificationEvent(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    APPROVAL_PENDING = "approval_pending"


class HealthStatus(str, Enum):
    """Agent 健康状态。"""
    READY = "READY"
    NOT_READY = "NOT_READY"
    STARTING = "STARTING"
    DEAD = "DEAD"


class AgentStatus(str, Enum):
    """Agent 运行时状态。"""
    IDLE = "IDLE"
    WORKING = "WORKING"
    ERROR = "ERROR"
    DEAD = "DEAD"
    CANCELLING = "CANCELLING"
