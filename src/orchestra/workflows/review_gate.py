# DETERMINISM REQUIRED — see CLAUDE.md §3
"""reviewGate（评审门禁）执行体：review → 路由修复 → 重测 → 重审。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .pipeline_base import PipelineWorkflowBase
from .pipeline_types import PipelineRunInput

with workflow.unsafe.imports_passed_through():
    from ..activities.agent_task import AgentTaskInput, execute_agent_task
    from ..domain.pipeline import GlobalSpec, Stage


class ReviewGateMixin(PipelineWorkflowBase):
    async def _run_review_gate(
        self,
        stage: Stage,
        stage_map: dict[str, Stage],
        inp: PipelineRunInput,
        global_: GlobalSpec,
    ) -> None:
        """执行 review gate：review → pass 则通过；fail 则路由 issue 给修复 Agent → 重测 → 重审。

        对齐 design.md §"Review Gate（评审门禁）"。
        """
        gate = stage.reviewGate
        assert gate is not None

        with workflow.unsafe.imports_passed_through():
            from ..domain.review import ReviewResult
            from ..domain.state import TaskInput
            from ..observability.tracing import inject_context

        pipeline_name = inp.pipeline_dict.get("metadata", {}).get("name", "unknown")
        wf_id = workflow.info().workflow_id

        for iteration in range(gate.maxIterations):
            if self._cancelled:
                raise asyncio.CancelledError()

            # 1. 执行 review agent
            review_agent = gate.agent or "blueberry"
            carrier: dict[str, str] = {}
            inject_context(carrier)

            review_input = self._get_state(str(gate.input)) if gate.input else self._state
            review_task = TaskInput(
                workflow_id=wf_id,
                stage_name=f"{stage.name}-rev",
                agent_name=review_agent,
                role="chat",
                tools=[],
                input=review_input,
                idempotency_key=f"{wf_id}/{stage.name}/review/{iteration}",
                traceparent=carrier.get("traceparent"),
                output_schema=gate.outputSchema,
                prompt=gate.prompt,
            )
            review_output = await workflow.execute_activity(
                execute_agent_task,
                AgentTaskInput(
                    task=review_task,
                    stage_name=f"{stage.name}-rev",
                    pipeline_name=pipeline_name,
                    output_schema=gate.outputSchema,
                ),
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=10),
                ),
            )

            # 2. 解析 ReviewResult
            try:
                result = ReviewResult.model_validate(review_output.output_value)
            except Exception as e:
                with workflow.unsafe.imports_passed_through():
                    from temporalio.exceptions import ApplicationError
                raise ApplicationError(
                    f"review gate '{stage.name}': ReviewResult 解析失败: {e}",
                    non_retryable=True,
                ) from e

            self._set_state(f"$.{stage.name}", result.model_dump())

            # 3. 检查 verdict
            verdict = result.verdict.value if hasattr(result.verdict, "value") else result.verdict
            if verdict == "pass":
                return  # 门禁通过

            # 4. 按 owner 路由 issue 给修复 Agent
            for i, issue in enumerate(result.issues):
                owner = issue.owner.value if hasattr(issue.owner, "value") else issue.owner
                fix_agent = gate.routing.get(owner)
                if not fix_agent:
                    continue

                carrier2: dict[str, str] = {}
                inject_context(carrier2)

                fix_task = TaskInput(
                    workflow_id=wf_id,
                    stage_name=f"{stage.name}-fx-{i}",
                    agent_name=fix_agent,
                    role=owner,
                    tools=[],
                    input=issue.model_dump(),
                    idempotency_key=f"{wf_id}/{stage.name}/fix/{issue.id}/{iteration}",
                    traceparent=carrier2.get("traceparent"),
                    prompt=(
                        f"修复问题 {issue.id}: {issue.problem}\n"
                        f"建议方案: {issue.suggestion or '自行判断'}"
                    ),
                )
                try:
                    fix_output = await workflow.execute_activity(
                        execute_agent_task,
                        AgentTaskInput(
                            task=fix_task,
                            stage_name=f"{stage.name}-fx-{i}",
                            pipeline_name=pipeline_name,
                        ),
                        start_to_close_timeout=timedelta(minutes=30),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    self._set_state(
                        f"$.{stage.name}.fixes.{issue.id}",
                        fix_output.output_value,
                    )
                except Exception:
                    pass  # 单个 issue 修复失败不阻断其他

            # 5. 重跑 retest stages
            for retest_name in gate.retest:
                retest_stage = stage_map.get(retest_name)
                if not retest_stage:
                    continue
                retest_output = await self._execute_stage(retest_stage, inp, global_)
                out_path = retest_name
                if isinstance(retest_stage.output, str):
                    out_path = retest_stage.output
                elif retest_stage.output:
                    out_path = retest_stage.output.path
                self._set_state(out_path, retest_output.output_value)

        # maxIterations reached
        if gate.onMaxReached == "fail":
            with workflow.unsafe.imports_passed_through():
                from temporalio.exceptions import ApplicationError
            raise ApplicationError(
                f"review gate '{stage.name}' 达到最大迭代次数 {gate.maxIterations}",
                non_retryable=True,
            )
