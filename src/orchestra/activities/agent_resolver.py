"""agentSelector 能力解析 Activity。

在 Workflow 中需要按 role + capabilities 匹配 Agent 时调用此 Activity。
由于 Workflow 代码受确定性约束，不能直接做网络 IO 或访问全局可变状态，
能力发现必须包装成 Activity。

每调用一次 = 对所有已注册 Agent 做一轮 get_capabilities()，
缓存结果用于同一 Workflow 内的后续 selector 匹配（减少 MCP 往返）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from temporalio import activity

from ..adapters.registry import list_adapter_names, get_adapter
from ..observability.logging import get_logger

logger = get_logger(__name__)

# 单次 Activity 调用内的能力缓存（同一 Workflow 多次 resolve 复用）
_cache: dict[str, dict] = {}


@dataclass
class AgentResolveInput:
    """agentSelector 解析请求。

    字段对应 domain.agent.AgentSelector，但使用纯 Python 类型
    以避免 Temporal 反序列化 Pydantic 对象的问题。
    """
    role: str | None = None
    capabilities: list[str] = field(default_factory=list)


@dataclass
class ResolvedAgent:
    """解析结果：匹配到的 Agent 及其路由信息。"""
    agent_name: str
    task_queue: str
    role: str
    capabilities: list[str] = field(default_factory=list)
    model: str | None = None


@activity.defn
async def resolve_agent_by_selector(inp: AgentResolveInput) -> ResolvedAgent:
    """按 role + capabilities 解析匹配的 Agent。

    对所有已注册 Agent 调用 get_capabilities()，筛选匹配项，
    返回第一个匹配的 Agent。如果多个 Agent 匹配，调用方（Workflow）
    负责通过 workflow.random() 做负载均衡（避免 Activity 内引入随机性）。

    三件套：
      - ① 心跳（首行）
      - ② 幂等查询
      - ③ 周期心跳 + 取消检查
    """
    # ① 心跳
    activity.heartbeat({"phase": "resolving", "role": inp.role, "capabilities": inp.capabilities})

    import asyncio
    from ..state.idempotency import get_store

    info = activity.info()
    store = get_store()

    # ② 幂等查询
    idem_key = f"agent-resolve/{info.workflow_id}/{info.activity_id}"
    if cached := await store.get(idem_key):
        logger.info("agent_resolve_cache_hit", key=idem_key)
        return ResolvedAgent(**cached)

    # ③ 能力发现（带取消检查 + 心跳）
    matches: list[ResolvedAgent] = []
    adapter_names = list_adapter_names()

    if activity.is_cancelled():
        from temporalio.exceptions import ActivityCancellationError
        raise ActivityCancellationError()

    activity.heartbeat({"phase": "discovering", "adapters_total": len(adapter_names)})

    for idx, name in enumerate(adapter_names):
        if activity.is_cancelled():
            from temporalio.exceptions import ActivityCancellationError
            raise ActivityCancellationError()

        try:
            adapter = get_adapter(name)
            caps = await asyncio.wait_for(
                adapter.get_capabilities(),
                timeout=10.0,
            )

            # 角色匹配
            if inp.role and caps.role.value != inp.role:
                continue

            # 能力匹配
            if inp.capabilities:
                agent_caps = set(caps.capabilities)
                if not all(c in agent_caps for c in inp.capabilities):
                    continue

            # 获取 task_queue（优先用 profile 中的配置，其次用 agent-name）
            from ..adapters.registry import get_profile_task_queue
            task_queue = get_profile_task_queue(name)

            matches.append(ResolvedAgent(
                agent_name=name,
                task_queue=task_queue,
                role=caps.role.value,
                capabilities=caps.capabilities,
                model=caps.model,
            ))

            activity.heartbeat({
                "phase": "discovering",
                "adapters_checked": idx + 1,
                "adapters_total": len(adapter_names),
                "matches_found": len(matches),
            })

        except asyncio.TimeoutError:
            logger.warning("agent_capabilities_timeout", agent=name)
            continue
        except Exception as e:
            logger.warning("agent_capabilities_failed", agent=name, error=str(e))
            continue

    if not matches:
        error_msg = f"没有 Agent 匹配 agentSelector role={inp.role!r} capabilities={inp.capabilities!r}"
        logger.error("agent_resolve_no_match", role=inp.role, capabilities=inp.capabilities)
        # 用非可重试错误终止
        from temporalio.exceptions import ApplicationError
        raise ApplicationError(error_msg, non_retryable=True)

    result = matches[0]  # 调用方 Workflow 负责多选一

    # ④ 写幂等缓存
    await store.put(idem_key, {
        "agent_name": result.agent_name,
        "task_queue": result.task_queue,
        "role": result.role,
        "capabilities": result.capabilities,
        "model": result.model,
    }, ttl_seconds=300)  # 短 TTL，同一 Workflow 内复用即可

    activity.heartbeat({"phase": "resolved", "agent": result.agent_name, "task_queue": result.task_queue})
    logger.info("agent_resolved", selector=f"role={inp.role} caps={inp.capabilities}",
                agent=result.agent_name, task_queue=result.task_queue)

    return result


@dataclass
class AgentResolveAllOutput:
    """批量解析结果：返回所有匹配项供 Workflow 负载均衡。"""
    matches: list[ResolvedAgent] = field(default_factory=list)


@activity.defn
async def resolve_all_matching_agents(inp: AgentResolveInput) -> AgentResolveAllOutput:
    """同 resolve_agent_by_selector，但返回全部匹配项。

    Workflow 调用此 Activity 后可用 workflow.random() 在 candidates 中
    随机选一个，实现简单的负载均衡。
    """
    # ① 心跳
    activity.heartbeat({"phase": "resolving_all", "role": inp.role})

    import asyncio
    from ..state.idempotency import get_store

    info = activity.info()
    store = get_store()

    idem_key = f"agent-resolve-all/{info.workflow_id}/{info.activity_id}"
    if cached := await store.get(idem_key):
        return AgentResolveAllOutput(
            matches=[ResolvedAgent(**m) for m in cached.get("matches", [])]
        )

    matches: list[ResolvedAgent] = []
    adapter_names = list_adapter_names()

    if activity.is_cancelled():
        from temporalio.exceptions import ActivityCancellationError
        raise ActivityCancellationError()

    for idx, name in enumerate(adapter_names):
        if activity.is_cancelled():
            raise ActivityCancellationError()

        try:
            adapter = get_adapter(name)
            caps = await asyncio.wait_for(adapter.get_capabilities(), timeout=10.0)

            if inp.role and caps.role.value != inp.role:
                continue
            if inp.capabilities:
                agent_caps = set(caps.capabilities)
                if not all(c in agent_caps for c in inp.capabilities):
                    continue

            from ..adapters.registry import get_profile_task_queue
            task_queue = get_profile_task_queue(name)

            matches.append(ResolvedAgent(
                agent_name=name,
                task_queue=task_queue,
                role=caps.role.value,
                capabilities=caps.capabilities,
                model=caps.model,
            ))

            activity.heartbeat({
                "phase": "discovering",
                "adapters_checked": idx + 1,
                "adapters_total": len(adapter_names),
                "matches_found": len(matches),
            })

        except (asyncio.TimeoutError, Exception):
            continue

    if not matches:
        from temporalio.exceptions import ApplicationError
        raise ApplicationError(
            f"没有 Agent 匹配 agentSelector role={inp.role!r} capabilities={inp.capabilities!r}",
            non_retryable=True,
        )

    await store.put(idem_key, {
        "matches": [
            {
                "agent_name": m.agent_name,
                "task_queue": m.task_queue,
                "role": m.role,
                "capabilities": m.capabilities,
                "model": m.model,
            }
            for m in matches
        ]
    }, ttl_seconds=300)

    activity.heartbeat({"phase": "resolved_all", "match_count": len(matches)})
    return AgentResolveAllOutput(matches=matches)
