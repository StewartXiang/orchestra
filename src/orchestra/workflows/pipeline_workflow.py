# DETERMINISM REQUIRED — see CLAUDE.md §3
"""PipelineWorkflow — 主 Workflow 定义。

⚠️ 绝对禁止：time.now / random / 文件IO / 网络IO / 全局可变状态
   参见 CLAUDE.md §3 确定性铁律

信号：cancel / pause / resume / override
查询：get_progress / get_dag_status / get_approval_status / get_state_size
更新：approve / reject（Temporal 1.21+，带返回值校验）

执行体按职责拆分到同级模块（保持单文件 ≤400 行，CLAUDE.md §5）：
  - pipeline_types     入口参数类型
  - stage_execution    单/多 Agent 执行 + agentSelector 路由 + 聚合
  - dynamic_execution  dynamic for_each
  - review_gate        reviewGate 评审门禁闭环
  - control_flow       loop / 子流水线 / 审批 / 补偿 / 通知 / State 读写
  - workflow_utils     纯工具（parse_duration）
"""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio import workflow

# ⚠️ Workflow 执行体（Mixin）必须在 passthrough 之外导入，由沙箱重新加载。
# 若放进 imports_passed_through()，这些方法会绑定宿主模块、绕过 Temporal 对
# datetime.now / random.random / asyncio.wait 等的确定性拦截（安全网失效）。
from .control_flow import ControlFlowMixin
from .dynamic_execution import DynamicExecutionMixin
from .review_gate import ReviewGateMixin
from .stage_execution import StageExecutionMixin

with workflow.unsafe.imports_passed_through():
    from ..domain.enums import OnFailure, Phase, StagePhase
    from ..domain.pipeline import GlobalSpec, Stage
    from ..schema.dag import topological_order
    from .pipeline_types import PipelineRunInput
    from .queries import (
        ApprovalStatusQuery,
        DagStatusQuery,
        ProgressQuery,
        StateSizeQuery,
    )
    from .signals import (
        ApproveUpdate,
        CancelSignal,
        OverrideSignal,
        PauseSignal,
        RejectUpdate,
        ResumeSignal,
    )
    from .updates import ApproveResult, RejectResult

__all__ = ["PipelineRunInput", "PipelineWorkflow"]


@workflow.defn
class PipelineWorkflow(
    StageExecutionMixin,
    DynamicExecutionMixin,
    ReviewGateMixin,
    ControlFlowMixin,
):
    """AI Agent 流水线主 Workflow。"""

    # ---------- 实例状态（只在 @workflow.run 内修改）----------
    def __init__(self) -> None:
        self._phase: Phase = Phase.PENDING
        self._current_stage: str = ""
        self._stage_statuses: dict[str, StagePhase] = {}
        self._state: dict[str, Any] = {}
        self._approval_state: dict[str, dict[str, Any]] = {}
        self._cancelled: bool = False
        self._paused: bool = False
        self._completed_count: int = 0

    # ---------- Signals ----------
    @workflow.signal
    async def cancel(self, sig: CancelSignal) -> None:
        self._cancelled = True

    @workflow.signal
    async def pause(self, sig: PauseSignal) -> None:
        self._paused = True

    @workflow.signal
    async def resume(self, sig: ResumeSignal) -> None:
        self._paused = False

    @workflow.signal
    async def override(self, sig: OverrideSignal) -> None:
        self._state[sig.key] = sig.value

    # ---------- Updates（Temporal 1.21+）----------
    @workflow.update
    async def approve(self, upd: ApproveUpdate) -> ApproveResult:
        """同步审批：校验 + 更新状态 + 返回结果。"""
        stage_name = upd.stage_name
        if stage_name not in self._approval_state:
            raise ValueError(f"stage '{stage_name}' 不需要审批或不存在")
        if self._approval_state[stage_name].get("status") != "pending":
            raise ValueError(f"stage '{stage_name}' 审批状态不是 pending")

        self._approval_state[stage_name]["approvals"].append(
            {
                "approver": upd.approver,
                "timestamp": str(workflow.now()),
            }
        )
        # 检查是否达到 policy 要求
        approval = self._get_stage_approval(stage_name)
        if approval and self._check_approval_policy(stage_name, approval):
            self._approval_state[stage_name]["status"] = "approved"

        return ApproveResult(approved_at=str(workflow.now()), approver=upd.approver)

    @workflow.update
    async def reject(self, upd: RejectUpdate) -> RejectResult:
        stage_name = upd.stage_name
        if stage_name not in self._approval_state:
            raise ValueError(f"stage '{stage_name}' 不需要审批或不存在")
        self._approval_state[stage_name]["status"] = "rejected"
        self._approval_state[stage_name]["reject_reason"] = upd.reason
        return RejectResult(
            rejected_at=str(workflow.now()), approver=upd.approver, reason=upd.reason
        )

    # ---------- Queries ----------
    @workflow.query
    def get_progress(self) -> ProgressQuery:
        return ProgressQuery(stage=self._current_stage, phase=self._phase.value, progress=0)

    @workflow.query
    def get_dag_status(self) -> DagStatusQuery:
        completed = [n for n, s in self._stage_statuses.items() if s == StagePhase.SUCCEEDED]
        running = [n for n, s in self._stage_statuses.items() if s == StagePhase.RUNNING]
        pending = [n for n, s in self._stage_statuses.items() if s == StagePhase.PENDING]
        skipped = [n for n, s in self._stage_statuses.items() if s == StagePhase.SKIPPED]
        failed = [n for n, s in self._stage_statuses.items() if s == StagePhase.FAILED]
        return DagStatusQuery(
            completed=completed, running=running, pending=pending, skipped=skipped, failed=failed
        )

    @workflow.query
    def get_approval_status(self, stage_name: str = "") -> ApprovalStatusQuery:
        if stage_name and stage_name in self._approval_state:
            a = self._approval_state[stage_name]
            return ApprovalStatusQuery(
                stage_name=stage_name,
                status=a.get("status", "pending"),
                approvers=a.get("approvals", []),
            )
        return ApprovalStatusQuery(stage_name=stage_name, status="not_required")

    @workflow.query
    def get_state_size(self) -> StateSizeQuery:
        import json

        size = len(json.dumps(self._state).encode())
        return StateSizeQuery(size_bytes=size, warning=size > 10_000_000)

    # ---------- Main ----------
    @workflow.run
    async def run(self, inp: PipelineRunInput) -> dict[str, Any]:
        # 还原 Pipeline 对象（从 dict，避免 Temporal 反序列化 Pydantic alias 问题）
        with workflow.unsafe.imports_passed_through():
            from ..schema.parser import parse_pipeline
        pipeline = parse_pipeline(inp.pipeline_dict)
        spec = pipeline.spec
        stages = spec.pipeline.stages

        # 恢复 continue_as_new 结转状态
        self._state = inp.carry_state or {"params": inp.params}
        self._completed_count = inp.completed_count
        self._stage_statuses = {
            name: StagePhase(status) for name, status in inp.stage_statuses.items()
        }
        for stage in stages:
            if stage.name not in self._stage_statuses:
                self._stage_statuses[stage.name] = StagePhase.PENDING

        self._phase = Phase.RUNNING

        try:
            await self._run_dag(inp, stages, spec.global_)
        except asyncio.CancelledError:
            self._phase = Phase.CANCELLED
            await self._notify(inp, "cancelled", spec.global_)
            return {"phase": Phase.CANCELLED.value}
        except Exception as exc:
            self._phase = Phase.COMPENSATING
            await self._run_compensation(inp, spec.pipeline.compensation)
            self._phase = Phase.FAILED
            await self._notify(inp, "failed", spec.global_, error=str(exc))
            # 确保用 ApplicationError 终止，避免 Temporal 无限重试 workflow task
            with workflow.unsafe.imports_passed_through():
                from temporalio.exceptions import ApplicationError
            if not isinstance(exc, ApplicationError):
                raise ApplicationError(str(exc), non_retryable=True) from exc
            raise

        self._phase = Phase.SUCCEEDED
        await self._notify(inp, "succeeded", spec.global_)
        return {"phase": Phase.SUCCEEDED.value, "state": self._state}

    # ---------- DAG 执行 ----------
    async def _run_dag(
        self,
        inp: PipelineRunInput,
        stages: list[Stage],
        global_: GlobalSpec,
    ) -> None:
        """按 DAG 拓扑顺序执行所有 Stage。"""
        topo = topological_order(stages)
        stage_map = {s.name: s for s in stages}
        failed_stages: list[str] = []
        skipped_stages: list[str] = []  # 用于 requireUpstream 级联跳过

        # 收集 loop 控制的 body stages（在循环内执行，不在主拓扑中执行）
        loop_body_stages: set[str] = set()
        for s in stages:
            if s.loop:
                for body_name in s.loop.body:
                    loop_body_stages.add(body_name)

        for stage_name in topo:
            # 取消检查
            if self._cancelled:
                raise asyncio.CancelledError()

            # 暂停等待
            if self._paused:
                self._phase = Phase.PAUSED
                await workflow.wait_condition(lambda: not self._paused)
                self._phase = Phase.RUNNING

            stage = stage_map[stage_name]

            # 跳过 loop body stages（由父 loop 节点在循环内执行）
            if stage_name in loop_body_stages:
                continue

            if self._should_skip_stage(stage, stage_name, failed_stages, skipped_stages):
                continue

            # ── loop 受限循环 ──
            if stage.loop:
                try:
                    await self._run_loop(stage, stage_map, inp, global_)
                    self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                    self._completed_count += 1
                except Exception as exc:
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
                    if stage.onFailure == OnFailure.FAIL:
                        with workflow.unsafe.imports_passed_through():
                            from temporalio.exceptions import ApplicationError
                        raise ApplicationError(str(exc), non_retryable=False) from exc
                continue

            # ── reviewGate 评审门禁 ──
            if stage.reviewGate:
                try:
                    await self._run_review_gate(stage, stage_map, inp, global_)
                    self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                    self._completed_count += 1
                except Exception as exc:
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
                    if stage.onFailure == OnFailure.FAIL:
                        with workflow.unsafe.imports_passed_through():
                            from temporalio.exceptions import ApplicationError
                        raise ApplicationError(str(exc), non_retryable=False) from exc
                continue

            # ── agentSelector 能力路由 ──
            resolved_agent: str | None = None
            resolved_queue: str | None = None  # None = 使用 Workflow 默认队列
            if stage.agentSelector and not stage.agent and not stage.agents:
                resolved_agent, resolved_queue = await self._resolve_agent_selector(stage)

            # 子流水线（childWorkflow）
            if stage.childWorkflow:
                try:
                    child_output = await self._run_child_workflow(stage, inp)
                    out_path = f"$.{stage_name}"
                    if stage.output:
                        out_path = (
                            stage.output if isinstance(stage.output, str) else stage.output.path
                        )
                    self._set_state(out_path, child_output)
                    self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                    self._completed_count += 1
                except Exception as exc:
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
                    if stage.onFailure == OnFailure.FAIL:
                        with workflow.unsafe.imports_passed_through():
                            from temporalio.exceptions import ApplicationError
                        raise ApplicationError(str(exc), non_retryable=False) from exc
                continue

            # 审批节点
            if stage.approval:
                await self._run_approval(stage, inp)
                approval_status = self._approval_state.get(stage_name, {}).get("status")
                if approval_status == "rejected":
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
                    if stage.onFailure == OnFailure.FAIL:
                        # 用 ApplicationError 正确终止 Workflow（不触发 Temporal 重试）
                        with workflow.unsafe.imports_passed_through():
                            from temporalio.exceptions import ApplicationError
                        raise ApplicationError(
                            f"stage '{stage_name}' 审批被拒绝",
                            non_retryable=True,
                        )
                    continue
                self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                continue

            # dynamic for_each
            if stage.dynamic:
                try:
                    results = await self._execute_dynamic(stage, inp, global_)
                    out_path = f"$.{stage_name}"
                    if stage.dynamic.aggregateOutput:
                        out_path = stage.dynamic.aggregateOutput
                    self._set_state(out_path, results)
                    self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                    self._completed_count += 1
                except Exception as exc:
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
                    if stage.onFailure == OnFailure.FAIL:
                        with workflow.unsafe.imports_passed_through():
                            from temporalio.exceptions import ApplicationError
                        raise ApplicationError(str(exc), non_retryable=False) from exc
                continue

            # 执行 Stage
            self._current_stage = stage_name
            self._stage_statuses[stage_name] = StagePhase.RUNNING

            try:
                output = await self._execute_stage(
                    stage,
                    inp,
                    global_,
                    resolved_agent=resolved_agent,
                    resolved_queue=resolved_queue,
                )
                # 写入 State
                out_path = (
                    stage_name
                    if isinstance(stage.output, str) and stage.output
                    else f"$.{stage_name}"
                )
                if isinstance(stage.output, str):
                    out_path = stage.output
                elif stage.output:
                    out_path = stage.output.path
                self._set_state(out_path, output.output_value)
                self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                self._completed_count += 1
            except Exception:
                self._stage_statuses[stage_name] = StagePhase.FAILED
                failed_stages.append(stage_name)
                if stage.onFailure == OnFailure.FAIL:
                    raise
                if stage.onFailure == OnFailure.COMPENSATE:
                    raise

            # continue_as_new 检查（每 100 stage 截断一次）
            if self._completed_count % 100 == 0 and self._completed_count > 0:
                workflow.continue_as_new(
                    PipelineRunInput(
                        pipeline_dict=inp.pipeline_dict,
                        run_id=inp.run_id,
                        params=inp.params,
                        actor=inp.actor,
                        carry_state=self._state,
                        completed_stages=list(self._stage_statuses.keys()),
                        stage_statuses={k: v.value for k, v in self._stage_statuses.items()},
                        completed_count=self._completed_count,
                    )
                )
