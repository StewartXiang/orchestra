# DETERMINISM REQUIRED — see CLAUDE.md §3
"""PipelineWorkflow 各执行体 Mixin 的共享基类。

仅承载**类型信息**：实例状态声明 + 跨 Mixin 方法签名。运行时这些方法都由具体
Mixin 覆盖（``PipelineWorkflowBase`` 在 MRO 末尾），基类里的桩实现永不执行（都会被具体 Mixin 覆盖）。

为什么需要它：拆分后各 Mixin 会互相调用（如 ``ControlFlowMixin._run_loop`` 调
``StageExecutionMixin._execute_stage``），mypy --strict 需要在这些方法所属的类上
看得到对端签名，否则报 ``has no attribute``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .pipeline_types import PipelineRunInput

if TYPE_CHECKING:
    from ..domain.enums import Phase, StagePhase
    from ..domain.pipeline import Compensation, GlobalSpec, Stage
    from ..domain.state import StageOutput


class PipelineWorkflowBase:
    """实例状态 + 跨 Mixin 方法签名（仅类型层）。"""

    # ---------- 实例状态（实际在 PipelineWorkflow.__init__ 初始化）----------
    _phase: Phase
    _current_stage: str
    _stage_statuses: dict[str, StagePhase]
    _state: dict[str, Any]
    _approval_state: dict[str, dict[str, Any]]
    _cancelled: bool
    _paused: bool
    _completed_count: int

    # ---------- stage_execution ----------
    async def _resolve_agent_selector(self, stage: Stage) -> tuple[str, str | None]:
        raise NotImplementedError

    async def _execute_stage(
        self,
        stage: Stage,
        inp: PipelineRunInput,
        global_: GlobalSpec,
        *,
        resolved_agent: str | None = None,
        resolved_queue: str | None = None,
    ) -> StageOutput:
        raise NotImplementedError

    # ---------- dynamic_execution ----------
    async def _execute_dynamic(
        self, stage: Stage, inp: PipelineRunInput, global_: GlobalSpec
    ) -> list[Any]:
        raise NotImplementedError

    # ---------- review_gate ----------
    async def _run_review_gate(
        self,
        stage: Stage,
        stage_map: dict[str, Stage],
        inp: PipelineRunInput,
        global_: GlobalSpec,
    ) -> None:
        raise NotImplementedError

    # ---------- control_flow ----------
    async def _run_loop(
        self,
        stage: Stage,
        stage_map: dict[str, Stage],
        inp: PipelineRunInput,
        global_: GlobalSpec,
    ) -> None:
        raise NotImplementedError

    async def _run_child_workflow(self, stage: Stage, inp: PipelineRunInput) -> Any:
        raise NotImplementedError

    async def _run_approval(self, stage: Stage, inp: PipelineRunInput) -> None:
        raise NotImplementedError

    async def _run_compensation(self, inp: PipelineRunInput, comp: Compensation | None) -> None:
        raise NotImplementedError

    async def _notify(
        self,
        inp: PipelineRunInput,
        event: str,
        global_: GlobalSpec,
        error: str | None = None,
    ) -> None:
        raise NotImplementedError

    def _get_state(self, path: str) -> Any:
        raise NotImplementedError

    def _set_state(self, path: str, value: Any) -> None:
        raise NotImplementedError

    def _should_skip_stage(
        self,
        stage: Stage,
        stage_name: str,
        failed_stages: list[str],
        skipped_stages: list[str],
    ) -> bool:
        raise NotImplementedError

    def _check_approval_policy(self, stage_name: str) -> bool:
        raise NotImplementedError
