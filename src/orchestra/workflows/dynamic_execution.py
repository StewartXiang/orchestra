# DETERMINISM REQUIRED — see CLAUDE.md §3
"""dynamic（for_each）阶段执行体。"""

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


class DynamicExecutionMixin(PipelineWorkflowBase):
    async def _execute_dynamic(
        self,
        stage: Stage,
        inp: PipelineRunInput,
        global_: GlobalSpec,
    ) -> list[Any]:
        """展开 dynamic.for_each，并行执行每个 item，返回结果列表。"""
        dynamic = stage.dynamic
        assert dynamic is not None

        # 从 State 获取 items 列表
        items = self._get_state(dynamic.input)
        if not isinstance(items, list):
            items = [items] if items is not None else []

        # 上限检查
        max_items = dynamic.maxItems or 1000
        items = items[:max_items]

        pipeline_name = inp.pipeline_dict.get("metadata", {}).get("name", "unknown")
        wf_id = workflow.info().workflow_id

        with workflow.unsafe.imports_passed_through():
            from ..observability.tracing import inject_context
        carrier: dict[str, str] = {}
        inject_context(carrier)

        timeout = timedelta(minutes=10)
        if dynamic.template and isinstance(dynamic.template, dict):
            tmpl_timeouts = dynamic.template.get("timeouts", {})
            if tmpl_timeouts.get("startToClose"):
                timeout = parse_duration(tmpl_timeouts["startToClose"])

        retry_policy = RetryPolicy(
            maximum_attempts=2,
            initial_interval=timedelta(seconds=5),
            maximum_interval=timedelta(minutes=2),
            backoff_coefficient=2.0,
        )

        # 从 template 中取 agent 名（支持静态 agent 或 agentSelector）
        tmpl = dynamic.template if isinstance(dynamic.template, dict) else {}
        static_agent = tmpl.get("agent")
        has_selector = "agentSelector" in tmpl

        # 模板展开函数（Workflow 内用 unsafe 绕过沙箱）
        with workflow.unsafe.imports_passed_through():
            from ..activities.agent_resolver import (
                AgentResolveAllOutput,
                AgentResolveInput,
                resolve_all_matching_agents,
            )
            from ..schema.template import render_dict

        # agentSelector 解析缓存：{(role, frozenset(caps)) → agent_name}
        _selector_cache: dict[tuple[Any, ...], str] = {}

        # 信号量控制并行度
        max_parallel = dynamic.maxParallel or 1
        sem = asyncio.Semaphore(max_parallel)

        async def _resolve_for_item(item: Any) -> str:
            """为单个 item 解析 agent：优先 template agent，其次 agentSelector。"""
            if static_agent:
                # 模板展开 agent 名（如 "walnut-{{ item.area }}"）
                expanded = render_dict(static_agent, {"item": item})
                return str(expanded)

            if has_selector:
                # 模板展开 agentSelector 各字段
                selector_raw = tmpl["agentSelector"]
                selector_expanded = render_dict(selector_raw, {"item": item})
                role = (
                    selector_expanded.get("role") if isinstance(selector_expanded, dict) else None
                )
                caps = (
                    selector_expanded.get("capabilities", [])
                    if isinstance(selector_expanded, dict)
                    else []
                )
                caps_key = frozenset(caps) if caps else frozenset()

                cache_key = (role, caps_key)
                if cache_key in _selector_cache:
                    return _selector_cache[cache_key]

                resolved: AgentResolveAllOutput = await workflow.execute_activity(
                    resolve_all_matching_agents,
                    AgentResolveInput(role=role, capabilities=list(caps_key)),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                if not resolved.matches:
                    with workflow.unsafe.imports_passed_through():
                        from temporalio.exceptions import ApplicationError
                    raise ApplicationError(
                        f"dynamic stage '{stage.name}': agentSelector 无匹配 "
                        f"(role={role}, capabilities={list(caps_key)})",
                        non_retryable=True,
                    )
                pick = resolved.matches[0].agent_name
                _selector_cache[cache_key] = pick
                return pick

            return "grape"  # 兜底

        async def _run_item(idx: int, item: Any) -> Any:
            async with sem:
                item_agent = await _resolve_for_item(item)
                task = TaskInput(
                    workflow_id=wf_id,
                    stage_name=f"{stage.name}_{idx}",
                    agent_name=item_agent,
                    role="developer",
                    tools=[],
                    input=item,
                    idempotency_key=f"{wf_id}/{stage.name}/{idx}",
                    traceparent=carrier.get("traceparent"),
                )
                result: StageOutput = await workflow.execute_activity(
                    execute_agent_task,
                    AgentTaskInput(
                        task=task, stage_name=f"{stage.name}_{idx}", pipeline_name=pipeline_name
                    ),
                    start_to_close_timeout=timeout,
                    retry_policy=retry_policy,
                )
                return result.output_value

        fail_fast = (dynamic.onItemFailure or "fail_fast") == "fail_fast"
        results = await asyncio.gather(
            *[_run_item(i, item) for i, item in enumerate(items)],
            return_exceptions=(not fail_fast),
        )

        if fail_fast:
            return list(results)

        # continue 模式：返回成功的，跳过异常
        return [r for r in results if not isinstance(r, BaseException)]
