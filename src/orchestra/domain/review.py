"""ReviewResult / ReviewIssue / ReviewGate 类型定义。

Review gate 将 review 从"文本建议"提升为"状态机门禁"：
  - ReviewResult.verdict == "pass" → 门禁通过
  - ReviewResult.verdict == "fail" → 按 issue.owner 路由给修复 Agent，重测 → 重审
  直到 pass 或达到 maxIterations。

参见 design.md §"Review Gate（评审门禁）"和 config/review-schema.yaml。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewSeverity(str, Enum):
    """问题严重程度。P0=阻断 P1=严重 P2=一般 P3=建议。"""
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ReviewArea(str, Enum):
    """问题所属领域。"""
    gameplay = "gameplay"
    visual = "visual"
    ui = "ui"
    technical = "technical"
    performance = "performance"
    audio = "audio"
    docs = "docs"


class ReviewVerdict(str, Enum):
    """评审结论：通过或驳回。"""
    PASS = "pass"
    FAIL = "fail"


class ReviewOwner(str, Enum):
    """问题路由目标角色。"""
    developer = "developer"
    designer = "designer"
    tester = "tester"
    artist = "artist"


class ReviewIssue(BaseModel):
    """单条评审问题。``owner`` 决定路由目标，``severity`` 决定优先级。"""
    model_config = ConfigDict(extra="forbid")

    id: str
    severity: ReviewSeverity
    owner: ReviewOwner
    area: ReviewArea
    problem: str
    suggestion: str | None = None
    acceptance: list[str] | None = None


class ReviewResult(BaseModel):
    """结构化评审结果。Pipeline 通过 verdict 做门禁决策。"""
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    confidence: float | None = Field(default=None, ge=0, le=1)
    summary: str | None = None
    issues: list[ReviewIssue] = Field(default_factory=list)


class ReviewGate(BaseModel):
    """Stage 的 review-gate 执行体（第八种 body 类型）。

    将 review agent + issue routing + retest + iteration 打包为
    一等概念。对齐 design.md §"Review Gate"。
    """
    model_config = ConfigDict(extra="forbid")

    agent: str | None = None
    agentSelector: dict[str, Any] | None = None
    input: str | dict[str, Any] | list[Any] | None = None
    prompt: str | None = None
    outputSchema: dict[str, Any] | None = None
    maxIterations: int = Field(default=5, ge=1, le=100)
    onMaxReached: str = "fail"
    routing: dict[str, str] = Field(default_factory=dict)
    retest: list[str] = Field(default_factory=list)
