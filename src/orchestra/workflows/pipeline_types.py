# DETERMINISM REQUIRED — see CLAUDE.md §3
"""PipelineWorkflow 入口参数类型（与 Temporal 反序列化兼容）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineRunInput:
    """Workflow 入口参数。

    注意：pipeline 字段存为 dict（JSON 序列化后传给 Temporal），
    避免 Pydantic alias 字段在 Temporal 反序列化时出错。
    Workflow 内部调用 _parse_pipeline(pipeline_dict) 还原为 Pipeline 对象。
    """

    pipeline_dict: dict[str, Any]  # Pipeline.model_dump(by_alias=True)
    run_id: str
    params: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    traceparent: str | None = None

    # continue_as_new 结转时的已有状态
    carry_state: dict[str, Any] = field(default_factory=dict)
    completed_stages: list[str] = field(default_factory=list)
    stage_statuses: dict[str, str] = field(default_factory=dict)
    completed_count: int = 0
