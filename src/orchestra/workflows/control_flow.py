# DETERMINISM REQUIRED — see CLAUDE.md §3
"""控制流辅助：loop 循环、子流水线、审批、补偿、通知与 State 读写。"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from .pipeline_base import PipelineWorkflowBase
from .pipeline_types import PipelineRunInput
from .workflow_utils import parse_duration

with workflow.unsafe.imports_passed_through():
    from ..activities.compensation import CompensationInput, run_compensation
    from ..activities.notification import NotificationInput, send_notification
    from ..domain.enums import OnFailure, Phase, StagePhase
    from ..domain.pipeline import Compensation, GlobalSpec, Stage


class ControlFlowMixin(PipelineWorkflowBase):
    async def _run_loop(
        self,
        stage: Stage,
        stage_map: dict[str, Stage],
        inp: PipelineRunInput,
        global_: GlobalSpec,
    ) -> None:
        """执行 loop 节点：按 condition 循环执行 body stages，不超过 maxIterations。

        对齐 design.md "迭代节点（受限循环）"：
          - 每轮按 body 顺序执行各 stage
          - 每轮结束后评估 condition
          - condition 为 False 时退出（成功）
          - 达到 maxIterations 时根据 onMaxReached 策略处理
        """
        loop = stage.loop
        assert loop is not None

        with workflow.unsafe.imports_passed_through():
            from ..schema.expr import evaluate as eval_expr

        for _iteration in range(loop.maxIterations):
            if self._cancelled:
                raise asyncio.CancelledError()

            # 执行 body 中每个 stage（按声明顺序）
            for body_name in loop.body:
                body_stage = stage_map[body_name]
                self._current_stage = body_name
                self._stage_statuses[body_name] = StagePhase.RUNNING

                try:
                    output = await self._execute_stage(body_stage, inp, global_)
                    out_path = body_name
                    if body_stage.output:
                        if isinstance(body_stage.output, str):
                            out_path = body_stage.output
                        else:
                            out_path = body_stage.output.path
                    self._set_state(out_path, output.output_value)
                    self._stage_statuses[body_name] = StagePhase.SUCCEEDED
                except Exception:
                    self._stage_statuses[body_name] = StagePhase.FAILED
                    raise

            # 评估是否继续循环
            try:
                should_continue = eval_expr(loop.condition, self._state)
            except Exception as e:
                with workflow.unsafe.imports_passed_through():
                    from temporalio.exceptions import ApplicationError
                raise ApplicationError(
                    f"loop '{stage.name}' condition 求值失败: {e}",
                    non_retryable=True,
                ) from e

            if not should_continue:
                return  # 条件满足，退出循环成功
        else:
            # maxIterations reached
            if loop.onMaxReached == "fail":
                with workflow.unsafe.imports_passed_through():
                    from temporalio.exceptions import ApplicationError
                raise ApplicationError(
                    f"loop '{stage.name}' 达到最大迭代次数 {loop.maxIterations}",
                    non_retryable=True,
                )
            # onMaxReached == "continue": 循环结束，视为完成

    async def _run_child_workflow(
        self,
        stage: Stage,
        inp: PipelineRunInput,
    ) -> Any:
        """执行子流水线（Child Workflow）。

        将当前 State 和子流水线引用传给 ChildPipelineWorkflow，
        子 Workflow 在自己的 Event History 中独立运行。
        """
        child_ref = stage.childWorkflow
        assert child_ref is not None

        # 从 State 提取子流水线输入
        child_input = self._get_state(str(stage.input)) if stage.input else self._state

        # parentClosePolicy 映射
        with workflow.unsafe.imports_passed_through():
            from ..domain.enums import ParentClosePolicy
        policy_map = {
            ParentClosePolicy.TERMINATE: workflow.ParentClosePolicy.TERMINATE,
            ParentClosePolicy.ABANDON: workflow.ParentClosePolicy.ABANDON,
            ParentClosePolicy.REQUEST_CANCEL: workflow.ParentClosePolicy.REQUEST_CANCEL,
        }
        parent_policy = policy_map.get(
            child_ref.parentClosePolicy, workflow.ParentClosePolicy.TERMINATE
        )

        # 构建子 Workflow 参数
        with workflow.unsafe.imports_passed_through():
            from .child_workflows import ChildPipelineWorkflow
        child_params = {
            "pipeline_name": child_ref.name,
            "pipeline_version": child_ref.version,
            "parent_run_id": inp.run_id,
            "input": child_input,
        }

        result = await workflow.execute_child_workflow(
            ChildPipelineWorkflow.run,
            child_params,
            parent_close_policy=parent_policy,
        )
        return result

    async def _run_approval(self, stage: Stage, inp: PipelineRunInput) -> None:
        """等待审批 Update 信号。"""
        stage_name = stage.name

        # 开发模式：approvers 包含占位符时自动审批
        if stage.approval and stage.approval.approvers:
            is_dev = all(
                a.startswith("ou_") or a.startswith("dev_") for a in stage.approval.approvers
            )
            if is_dev:
                self._approval_state[stage_name] = {
                    "status": "approved",
                    "approvals": [{"approver": "system", "reason": "dev auto-approve"}],
                    "started_at": str(workflow.now()),
                }
                return

        self._approval_state[stage_name] = {
            "status": "pending",
            "approvals": [],
            "started_at": str(workflow.now()),
        }
        self._phase = Phase.PENDING_APPROVAL

        timeout = timedelta(hours=1)
        if stage.approval and stage.approval.timeout:
            timeout = parse_duration(stage.approval.timeout)

        try:
            await workflow.wait_condition(
                lambda: self._approval_state[stage_name]["status"] != "pending",
                timeout=timeout,
            )
        except TimeoutError:
            on_timeout = stage.approval.onTimeout.value if stage.approval else "reject"
            self._approval_state[stage_name]["status"] = (
                "rejected" if on_timeout in ("reject", "escalate") else "approved"
            )

        self._phase = Phase.RUNNING

    def _get_stage_approval(self, stage_name: str) -> Any:
        pipeline_stages = {}
        return pipeline_stages.get(stage_name)

    def _check_approval_policy(self, stage_name: str, approval: Any) -> bool:
        approvals = self._approval_state.get(stage_name, {}).get("approvals", [])
        return len(approvals) >= 1  # 简化：至少一个审批

    async def _run_compensation(self, inp: PipelineRunInput, comp: Compensation | None) -> None:
        if not comp:
            return
        for action in comp.actions:
            try:
                await workflow.execute_activity(
                    run_compensation,
                    CompensationInput(
                        for_stage=action.forStage,
                        agent_name=action.agent,
                        action=action.action,
                        input_data=self._state,
                    ),
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RetryPolicy(maximum_attempts=comp.maxCompensationAttempts),
                )
            except Exception:
                if comp.onCompensationFailure == "abort":
                    raise

    async def _notify(
        self,
        inp: PipelineRunInput,
        event: str,
        global_: GlobalSpec,
        error: str | None = None,
    ) -> None:
        if not global_.notification or not global_.notification.onEvents:
            return
        valid_events = [e.value for e in global_.notification.onEvents]
        if event not in valid_events:
            return
        _pipeline_name = inp.pipeline_dict.get("metadata", {}).get("name", "unknown")
        msg = f"Pipeline {_pipeline_name} [{event}]"
        if error:
            msg += f": {error}"
        for channel in global_.notification.channels:
            with contextlib.suppress(Exception):  # 通知失败不阻塞流水线
                await workflow.execute_activity(
                    send_notification,
                    NotificationInput(
                        channel=channel.value,
                        target=global_.notification.target or "",
                        message=msg,
                        pipeline_id=_pipeline_name,
                        event=event,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

    def _get_state(self, path: str) -> Any:
        if not path or not path.startswith("$"):
            return path
        with workflow.unsafe.imports_passed_through():
            from ..schema.jsonpath import get_value
        return get_value(self._state, path)

    def _set_state(self, path: str, value: Any) -> None:
        if not path:
            return
        if not path.startswith("$"):
            path = f"$.{path}"
        with workflow.unsafe.imports_passed_through():
            from ..schema.jsonpath import set_value
        try:
            set_value(self._state, path, value)
        except Exception as exc:
            # 不再静默：写失败会让下游 input 取不到值，必须留痕（Workflow logger 确定性安全）
            workflow.logger.warning(f"state 写入失败 path={path!r}: {exc}")

    def _should_skip_stage(
        self,
        stage: Stage,
        stage_name: str,
        failed_stages: list[str],
        skipped_stages: list[str],
    ) -> bool:
        """判断 stage 本轮是否应跳过（并登记其 status）。True 表示调用方 ``continue``。

        覆盖：continue_as_new 恢复的已完成节点、前驱失败（``onFailure=fail``）、
        ``requireUpstream`` 级联跳过、``condition`` 为假或求值失败。
        参见 design.md §"DAG 设计"。
        """
        # 已完成的 stage（continue_as_new 恢复）
        if self._stage_statuses.get(stage_name) in (
            StagePhase.SUCCEEDED,
            StagePhase.SKIPPED,
        ):
            return True

        # 前驱失败时跳过（onFailure=continue 的后继节点除外）
        if (
            any(dep in failed_stages for dep in stage.dependsOn)
            and stage.onFailure == OnFailure.FAIL
        ):
            self._stage_statuses[stage_name] = StagePhase.SKIPPED
            skipped_stages.append(stage_name)
            return True

        # 前驱 SKIPPED 且 requireUpstream=True 时级联跳过
        if stage.requireUpstream and any(dep in skipped_stages for dep in stage.dependsOn):
            self._stage_statuses[stage_name] = StagePhase.SKIPPED
            skipped_stages.append(stage_name)
            return True

        # condition 检查
        if stage.condition:
            with workflow.unsafe.imports_passed_through():
                from ..schema.expr import evaluate as eval_expr
            try:
                if not eval_expr(stage.condition, self._state):
                    self._stage_statuses[stage_name] = StagePhase.SKIPPED
                    skipped_stages.append(stage_name)
                    return True
            except Exception:
                self._stage_statuses[stage_name] = StagePhase.FAILED
                failed_stages.append(stage_name)
                return True

        return False
