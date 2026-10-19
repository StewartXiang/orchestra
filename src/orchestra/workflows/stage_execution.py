# DETERMINISM REQUIRED — see CLAUDE.md §3
"""Stage 执行体：agentSelector 能力路由 + 单/多 Agent 执行与结果聚合。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from .pipeline_base import PipelineWorkflowBase
from .pipeline_types import PipelineRunInput
from .workflow_utils import parse_duration

with workflow.unsafe.imports_passed_through():
    from ..activities.agent_task import AgentTaskInput, execute_agent_task
    from ..domain.pipeline import GlobalSpec, Stage
    from ..domain.state import StageOutput, TaskInput


class StageExecutionMixin(PipelineWorkflowBase):
    async def _resolve_agent_selector(
        self,
        stage: Stage,
    ) -> tuple[str, str | None]:
        """解析 agentSelector，返回 (agent_name, task_queue)。

        通过 Activity 调用所有 Agent 的 get_capabilities()，按 role + capabilities
        筛选匹配项。若仅一个匹配则直接返回；若多个匹配则用 workflow.random()
        做负载均衡。

        task_queue 返回 None 时表示使用 Workflow 的默认队列（适用于单 Worker 部署
        或测试环境）。仅在多 Worker 部署且 Agent 有独立 Task Queue 时才返回非 None。
        """
        selector = stage.agentSelector
        assert selector is not None

        role_val: str | None = selector.role.value if selector.role else None
        caps: list[str] = selector.capabilities or []

        with workflow.unsafe.imports_passed_through():
            from ..activities.agent_resolver import (
                AgentResolveAllOutput,
                AgentResolveInput,
                resolve_all_matching_agents,
            )

        all_matches: AgentResolveAllOutput = await workflow.execute_activity(
            resolve_all_matching_agents,
            AgentResolveInput(role=role_val, capabilities=caps),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                initial_interval=timedelta(seconds=5),
            ),
        )

        if not all_matches.matches:
            with workflow.unsafe.imports_passed_through():
                from temporalio.exceptions import ApplicationError
            raise ApplicationError(
                f"stage '{stage.name}': agentSelector 无匹配 Agent "
                f"(role={role_val}, capabilities={caps})",
                non_retryable=True,
            )

        # 负载均衡：单个直接返回，多个用 workflow.random() 随机选
        matches = all_matches.matches
        if len(matches) == 1:
            pick = matches[0]
        else:
            rnd = workflow.random()
            idx = rnd.randint(0, len(matches) - 1)
            pick = matches[idx]

        # task_queue 返回 None：当前使用 Workflow 默认队列。
        # 后续多 Worker 部署时启用 pick.task_queue 路由。
        return pick.agent_name, None

    async def _execute_stage(
        self,
        stage: Stage,
        inp: PipelineRunInput,
        global_: GlobalSpec,
        *,
        resolved_agent: str | None = None,
        resolved_queue: str | None = None,
    ) -> StageOutput:
        """执行单个 Stage（单 agent、并行多 agent、或 agentSelector 路由）。

        :param resolved_agent: agentSelector 解析后的 agent 名（覆盖 stage.agent）
        :param resolved_queue: agentSelector 解析后的 task_queue（路由到匹配 Agent）
        """
        # ── 构建通用参数 ──
        with workflow.unsafe.imports_passed_through():
            from ..observability.tracing import inject_context
        carrier: dict[str, str] = {}
        inject_context(carrier)

        pipeline_name = inp.pipeline_dict.get("metadata", {}).get("name", "unknown")
        wf_id = workflow.info().workflow_id
        stage_input = self._get_state(str(stage.input) if stage.input else "")

        retry_kwargs: dict[str, Any] = {
            "maximum_attempts": 3,
            "initial_interval": timedelta(seconds=10),
            "maximum_interval": timedelta(minutes=5),
            "backoff_coefficient": 2.0,
        }
        # 应用 Stage 级 RetryPolicy 覆盖 + nonRetryableErrors
        if stage.retry:
            retry_kwargs["maximum_attempts"] = stage.retry.maxAttempts
            if stage.retry.nonRetryableErrors:
                retry_kwargs["non_retryable_error_types"] = stage.retry.nonRetryableErrors
        retry_policy = RetryPolicy(**retry_kwargs)
        timeout = timedelta(minutes=30)
        if stage.timeouts and stage.timeouts.startToClose:
            timeout = parse_duration(stage.timeouts.startToClose)

        def _make_task(agent_name: str, suffix: str = "") -> AgentTaskInput:
            # 从 Agent spec 获取 role 和 tools（用于 prompt 模板和 TaskInput）
            agent_spec = inp.pipeline_dict.get("spec", {}).get("agents", {}).get(agent_name, {})
            agent_role = agent_spec.get("role", "developer")
            agent_tools = agent_spec.get("tools", [])

            # 模板展开 stage.prompt（支持 {{ input }}, {{ stage }}, {{ params.* }},
            #   {{ tools }}, {{ role }}, {{ agent }}）
            prompt_expanded: str | None = None
            if stage.prompt:
                with workflow.unsafe.imports_passed_through():
                    from ..schema.template import render
                prompt_expanded = render(
                    stage.prompt,
                    {
                        "input": stage_input,
                        "stage": stage.name,
                        "params": inp.params,
                        "tools": ", ".join(agent_tools),
                        "role": agent_role,
                        "agent": agent_name,
                    },
                )

            task = TaskInput(
                workflow_id=wf_id,
                stage_name=stage.name,
                agent_name=agent_name,
                role=agent_role,
                tools=agent_tools,
                input=stage_input,
                idempotency_key=f"{wf_id}/{stage.name}{suffix}",
                traceparent=carrier.get("traceparent"),
                output_schema=stage.outputSchema,
                prompt=prompt_expanded,
            )
            cache_enabled = bool(stage.cache and stage.cache.enabled)
            cache_ttl = 86400
            if stage.cache and stage.cache.ttl:
                cache_ttl = int(parse_duration(stage.cache.ttl).total_seconds())
            return AgentTaskInput(
                task=task,
                stage_name=stage.name,
                pipeline_name=pipeline_name,
                cache_enabled=cache_enabled,
                cache_ttl_seconds=cache_ttl,
                input_schema=stage.inputSchema,
                output_schema=stage.outputSchema,
                schema_violation_policy=stage.schemaViolationPolicy,
            )

        async def _run_one(
            agent_name: str, suffix: str = "", task_queue: str | None = None
        ) -> StageOutput:
            kwargs: dict[str, Any] = {
                "start_to_close_timeout": timeout,
                "retry_policy": retry_policy,
            }
            if task_queue:
                kwargs["task_queue"] = task_queue
            return await workflow.execute_activity(
                execute_agent_task,
                _make_task(agent_name, suffix),
                **kwargs,
            )

        # ── agentSelector 路由（已由 _run_dag 解析，直接使用）──
        if resolved_agent:
            # 仅在 resolved_queue 与本 Workflow 的 task_queue 不同时才显式路由
            # 否则走默认（使用 Workflow 的 task_queue），方便测试和单 Worker 部署
            wf_queue = workflow.info().task_queue
            actual_queue = resolved_queue if resolved_queue and resolved_queue != wf_queue else None
            return await _run_one(resolved_agent, task_queue=actual_queue)

        # ── 单 Agent ──
        if stage.agent or not stage.agents:
            agent_name = stage.agent or "grape"
            return await _run_one(agent_name)

        # ── 并行 Agents（aggregateStrategy + maxConcurrency 限流）──
        agents = stage.agents
        strategy = stage.aggregateStrategy
        max_parallel = global_.maxConcurrency

        results: list[StageOutput | BaseException] = []
        for chunk_start in range(0, len(agents), max_parallel):
            chunk = agents[chunk_start : chunk_start + max_parallel]
            chunk_results = list(
                await asyncio.gather(
                    *[_run_one(a, f"/{chunk_start + i}") for i, a in enumerate(chunk)],
                    return_exceptions=True,
                )
            )
            results.extend(chunk_results)

        successes = [r for r in results if isinstance(r, StageOutput)]
        errors = [r for r in results if isinstance(r, BaseException)]

        from ..domain.enums import AggregateStrategy

        if strategy == AggregateStrategy.ANY:
            if successes:
                return successes[0]
            raise errors[0]

        if strategy == AggregateStrategy.FIRST:
            if successes:
                return successes[0]
            raise errors[0]

        if strategy == AggregateStrategy.ALL:
            if errors:
                raise errors[0]
            # 合并所有成功输出为 list
            return StageOutput(
                stage_name=stage.name,
                success=True,
                output_path=f"$.{stage.name}",
                output_value=[r.output_value for r in successes],
                started_at_iso=successes[0].started_at_iso if successes else "",
                completed_at_iso=successes[-1].completed_at_iso if successes else "",
                tokens_consumed=sum(r.tokens_consumed for r in successes),
                cost_usd=sum(r.cost_usd for r in successes),
            )

        if strategy == AggregateStrategy.MERGE:
            # 将各输出合并为 dict
            merged: dict[str, Any] = {}
            for r in successes:
                if isinstance(r.output_value, dict):
                    merged.update(r.output_value)
            return StageOutput(
                stage_name=stage.name,
                success=True,
                output_path=f"$.{stage.name}",
                output_value=merged,
                started_at_iso=successes[0].started_at_iso if successes else "",
                completed_at_iso=successes[-1].completed_at_iso if successes else "",
                tokens_consumed=sum(r.tokens_consumed for r in successes),
                cost_usd=sum(r.cost_usd for r in successes),
            )

        if strategy in (AggregateStrategy.VOTE, AggregateStrategy.QUORUM):
            threshold = stage.quorumThreshold or 0.5
            needed = int(len(agents) * threshold) + 1
            if len(successes) >= needed:
                return successes[0]
            raise errors[0] if errors else RuntimeError(f"达不到 quorum 阈值 {threshold}")

        # 默认 all
        if errors:
            raise errors[0]
        return successes[0]
