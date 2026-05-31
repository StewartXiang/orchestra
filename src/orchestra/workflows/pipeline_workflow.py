# DETERMINISM REQUIRED — see CLAUDE.md §3
"""PipelineWorkflow — 主 Workflow 定义。

⚠️ 绝对禁止：time.now / random / 文件IO / 网络IO / 全局可变状态
   参见 CLAUDE.md §3 确定性铁律

信号：cancel / pause / resume / override
查询：get_progress / get_dag_status / get_approval_status / get_state_size
更新：approve / reject（Temporal 1.21+，带返回值校验）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    # Pydantic + Python stdlib 模块在 import 时会访问 os.environ/zoneinfo/_io，
    # 必须全部在沙箱外预加载，否则 Temporal 确定性检查会报 RestrictedWorkflowAccessError
    import pydantic, pydantic.plugin, pydantic.plugin._loader, pydantic._internal, pydantic.deprecated, pydantic_core  # noqa: F401
    import zoneinfo, sysconfig, platform, io, pathlib  # noqa: F401

    from ..activities.agent_task import AgentTaskInput, execute_agent_task
    from ..activities.audit import AuditInput, write_audit_log
    from ..activities.compensation import CompensationInput, run_compensation
    from ..activities.notification import NotificationInput, send_notification
    from ..domain.enums import OnFailure, Phase, StagePhase
    from ..domain.pipeline import Compensation, GlobalSpec, Pipeline, Stage
    from ..domain.state import StageOutput, TaskInput
    from ..schema.dag import DagValidationResult, parallel_groups, topological_order
    from ..workflows.queries import ApprovalStatusQuery, DagStatusQuery, ProgressQuery, StateSizeQuery
    from ..workflows.signals import ApproveUpdate, CancelSignal, OverrideSignal, PauseSignal, RejectUpdate, ResumeSignal
    from ..workflows.updates import ApproveResult, RejectResult


@dataclass
class PipelineRunInput:
    """Workflow 入口参数。

    注意：pipeline 字段存为 dict（JSON 序列化后传给 Temporal），
    避免 Pydantic alias 字段在 Temporal 反序列化时出错。
    Workflow 内部调用 _parse_pipeline(pipeline_dict) 还原为 Pipeline 对象。
    """
    pipeline_dict: dict[str, Any]   # Pipeline.model_dump(by_alias=True)
    run_id: str
    params: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    traceparent: str | None = None

    # continue_as_new 结转时的已有状态
    carry_state: dict[str, Any] = field(default_factory=dict)
    completed_stages: list[str] = field(default_factory=list)
    stage_statuses: dict[str, str] = field(default_factory=dict)
    completed_count: int = 0


@workflow.defn
class PipelineWorkflow:
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

        self._approval_state[stage_name]["approvals"].append({
            "approver": upd.approver,
            "timestamp": str(workflow.now()),
        })
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
        return RejectResult(rejected_at=str(workflow.now()), approver=upd.approver, reason=upd.reason)

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
        return DagStatusQuery(completed=completed, running=running, pending=pending, skipped=skipped, failed=failed)

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
        namespace = pipeline.metadata.name

        # 恢复 continue_as_new 结转状态
        self._state = inp.carry_state or {"params": inp.params}
        self._completed_count = inp.completed_count
        self._stage_statuses = {
            name: StagePhase(status)
            for name, status in inp.stage_statuses.items()
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

            # 跳过已完成（continue_as_new 恢复）
            if self._stage_statuses.get(stage_name) in (StagePhase.SUCCEEDED, StagePhase.SKIPPED):
                continue

            # 前驱失败时跳过（onFailure=continue 的后继节点除外）
            if any(dep in failed_stages for dep in stage.dependsOn):
                if stage.onFailure == OnFailure.FAIL:
                    self._stage_statuses[stage_name] = StagePhase.SKIPPED
                    skipped_stages.append(stage_name)
                    continue

            # 前驱 SKIPPED 且 requireUpstream=True 时级联跳过
            if stage.requireUpstream and any(dep in skipped_stages for dep in stage.dependsOn):
                self._stage_statuses[stage_name] = StagePhase.SKIPPED
                skipped_stages.append(stage_name)
                continue

            # condition 检查
            if stage.condition:
                with workflow.unsafe.imports_passed_through():
                    from ..schema.expr import evaluate as eval_expr
                try:
                    if not eval_expr(stage.condition, self._state):
                        self._stage_statuses[stage_name] = StagePhase.SKIPPED
                        skipped_stages.append(stage_name)
                        continue
                except Exception:
                    self._stage_statuses[stage_name] = StagePhase.FAILED
                    failed_stages.append(stage_name)
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
                        out_path = stage.output if isinstance(stage.output, str) else stage.output.path
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
                    stage, inp, global_,
                    resolved_agent=resolved_agent,
                    resolved_queue=resolved_queue,
                )
                # 写入 State
                out_path = stage_name if isinstance(stage.output, str) and stage.output else f"$.{stage_name}"
                if isinstance(stage.output, str):
                    out_path = stage.output
                elif stage.output:
                    out_path = stage.output.path
                self._set_state(out_path, output.output_value)
                self._stage_statuses[stage_name] = StagePhase.SUCCEEDED
                self._completed_count += 1
            except Exception as exc:
                self._stage_statuses[stage_name] = StagePhase.FAILED
                failed_stages.append(stage_name)
                if stage.onFailure == OnFailure.FAIL:
                    raise
                if stage.onFailure == OnFailure.COMPENSATE:
                    raise

            # continue_as_new 检查（每 100 stage 截断一次）
            if self._completed_count % 100 == 0 and self._completed_count > 0:
                workflow.continue_as_new(PipelineRunInput(
                    pipeline_dict=inp.pipeline_dict,
                    run_id=inp.run_id,
                    params=inp.params,
                    actor=inp.actor,
                    carry_state=self._state,
                    completed_stages=list(self._stage_statuses.keys()),
                    stage_statuses={k: v.value for k, v in self._stage_statuses.items()},
                    completed_count=self._completed_count,
                ))

    # ---------- dynamic for_each ----------

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
                timeout = _parse_duration(tmpl_timeouts["startToClose"])

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
            from ..schema.template import render_dict
            from ..activities.agent_resolver import (
                AgentResolveInput,
                AgentResolveAllOutput,
                resolve_all_matching_agents,
            )

        # agentSelector 解析缓存：{(role, frozenset(caps)) → agent_name}
        _selector_cache: dict[tuple, str] = {}

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
                role = selector_expanded.get("role") if isinstance(selector_expanded, dict) else None
                caps = selector_expanded.get("capabilities", []) if isinstance(selector_expanded, dict) else []
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
                    AgentTaskInput(task=task, stage_name=f"{stage.name}_{idx}", pipeline_name=pipeline_name),
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

    # ---------- agentSelector 能力路由 ----------

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
                AgentResolveInput,
                AgentResolveAllOutput,
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

    # ---------- loop 受限循环 ----------

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

        for iteration in range(loop.maxIterations):
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

    # ---------- reviewGate 评审门禁 ----------

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
                    from temporalio.exceptions import ApplicationError as _AE
                raise _AE(
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
                        f"$.{stage.name}.fixes.{issue.id}", fix_output.output_value,
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
                from temporalio.exceptions import ApplicationError as _AE
            raise _AE(
                f"review gate '{stage.name}' 达到最大迭代次数 {gate.maxIterations}",
                non_retryable=True,
            )

    # ---------- childWorkflow ----------

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
        parent_policy = policy_map.get(child_ref.parentClosePolicy, workflow.ParentClosePolicy.TERMINATE)

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

        retry_kwargs: dict = dict(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=10),
            maximum_interval=timedelta(minutes=5),
            backoff_coefficient=2.0,
        )
        # 应用 Stage 级 RetryPolicy 覆盖 + nonRetryableErrors
        if stage.retry:
            retry_kwargs["maximum_attempts"] = stage.retry.maxAttempts
            if stage.retry.nonRetryableErrors:
                retry_kwargs["non_retryable_error_types"] = stage.retry.nonRetryableErrors
        retry_policy = RetryPolicy(**retry_kwargs)
        timeout = timedelta(minutes=30)
        if stage.timeouts and stage.timeouts.startToClose:
            timeout = _parse_duration(stage.timeouts.startToClose)

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
                cache_ttl = int(_parse_duration(stage.cache.ttl).total_seconds())
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

        async def _run_one(agent_name: str, suffix: str = "", task_queue: str | None = None) -> StageOutput:
            kwargs: dict = dict(
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
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
            chunk_results = list(await asyncio.gather(
                *[_run_one(a, f"/{chunk_start + i}") for i, a in enumerate(chunk)],
                return_exceptions=True,
            ))
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
            merged: dict = {}
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

    # ---------- 审批 ----------

    async def _run_approval(self, stage: Stage, inp: PipelineRunInput) -> None:
        """等待审批 Update 信号。"""
        stage_name = stage.name

        # 开发模式：approvers 包含占位符时自动审批
        if stage.approval and stage.approval.approvers:
            is_dev = all(a.startswith("ou_") or a.startswith("dev_") for a in stage.approval.approvers)
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
            timeout = _parse_duration(stage.approval.timeout)

        try:
            await workflow.wait_condition(
                lambda: self._approval_state[stage_name]["status"] != "pending",
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            on_timeout = stage.approval.onTimeout.value if stage.approval else "reject"
            self._approval_state[stage_name]["status"] = "rejected" if on_timeout in ("reject", "escalate") else "approved"

        self._phase = Phase.RUNNING

    def _get_stage_approval(self, stage_name: str) -> Any:
        pipeline_stages = {}
        return pipeline_stages.get(stage_name)

    def _check_approval_policy(self, stage_name: str, approval: Any) -> bool:
        approvals = self._approval_state.get(stage_name, {}).get("approvals", [])
        return len(approvals) >= 1  # 简化：至少一个审批

    # ---------- 补偿 ----------

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

    # ---------- 通知 ----------

    async def _notify(
        self,
        inp: PipelineRunInput,
        event: str,
        global_: GlobalSpec,
        error: str | None = None,
    ) -> None:
        if not global_.notification or not global_.notification.onEvents:
            return
        with workflow.unsafe.imports_passed_through():
            from ..domain.enums import NotificationEvent
        valid_events = [e.value for e in global_.notification.onEvents]
        if event not in valid_events:
            return
        _pipeline_name = inp.pipeline_dict.get("metadata", {}).get("name", "unknown")
        msg = f"Pipeline {_pipeline_name} [{event}]"
        if error:
            msg += f": {error}"
        for channel in global_.notification.channels:
            try:
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
            except Exception:
                pass  # 通知失败不阻塞流水线

    # ---------- 工具 ----------

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
        except Exception:
            pass


def _parse_duration(d: str) -> timedelta:
    """将 Duration 字符串转为 timedelta。"""
    import re
    units = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    m = re.fullmatch(r"(\d+)(ns|us|ms|s|m|h|d|w)", d)
    if not m:
        return timedelta(minutes=30)  # 默认
    return timedelta(seconds=float(m.group(1)) * units[m.group(2)])
