"""Kahn 拓扑排序 / 环检测 / 孤儿节点 / 子流水线递归检测。

这是一个无 IO 的纯函数模块——所有输入都是 domain 对象，所有输出都是
ValidationResult 或 list[str]（错误消息）。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Sequence

from ..domain.pipeline import Pipeline, Stage


@dataclass
class DagValidationResult:
    """DAG 静态分析结果。``errors`` 非空则 pipeline 不可提交。"""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    topo_order: list[str] = field(default_factory=list)  # 执行顺序

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


def validate_dag(pipeline: Pipeline, visited_names: frozenset[str] | None = None) -> DagValidationResult:
    """全量 DAG 校验入口。

    :param visited_names: 递归检测子流水线时传入已访问的 pipeline 名称集合。
    """
    stages = pipeline.spec.pipeline.stages
    result = DagValidationResult()

    stage_names = {s.name for s in stages}

    # 1. 基础引用：dependsOn 里的名字必须存在
    for stage in stages:
        for dep in stage.dependsOn:
            if dep not in stage_names:
                result.errors.append(
                    f"stage '{stage.name}': dependsOn 引用了不存在的 stage '{dep}'"
                )

    # 2. 环检测 + 拓扑排序（Kahn 算法）
    topo, cycle_err = _kahn_sort(stages)
    if cycle_err:
        result.errors.append(cycle_err)
    else:
        result.topo_order = topo

    # 3. 孤儿节点（无 dependsOn、无被依赖、且不是首节点）
    has_dependents = set()
    for stage in stages:
        for dep in stage.dependsOn:
            has_dependents.add(dep)
    for stage in stages:
        if stage.dependsOn or stage.name in has_dependents:
            continue
        # 没有依赖也没有被依赖 → 只有一个 stage 时是合法的起点
        if len(stages) > 1:
            result.warnings.append(
                f"stage '{stage.name}' 看起来是孤儿节点（无依赖且无后继）"
            )

    # 4. loop.body 引用的 stage 名必须存在
    for stage in stages:
        if stage.loop:
            for body_name in stage.loop.body:
                if body_name not in stage_names:
                    result.errors.append(
                        f"stage '{stage.name}': loop.body 引用了不存在的 stage '{body_name}'"
                    )

    # 5. 子流水线递归检测（简化：检测 childWorkflow.name 是否等于自身或已访问集合）
    current_name = pipeline.metadata.name
    visited = visited_names or frozenset()
    visited = visited | {current_name}
    for stage in stages:
        if stage.childWorkflow:
            child_name = stage.childWorkflow.name
            if child_name in visited:
                result.errors.append(
                    f"stage '{stage.name}': 检测到子流水线递归引用 '{child_name}'"
                )

    return result


def topological_order(stages: Sequence[Stage]) -> list[str]:
    """仅返回拓扑顺序，调用者保证无环（或自行处理 ValueError）。"""
    order, err = _kahn_sort(stages)
    if err:
        raise ValueError(err)
    return order


def _kahn_sort(stages: Sequence[Stage]) -> tuple[list[str], str | None]:
    """Kahn 拓扑排序。返回 (顺序列表, 错误信息或 None)。"""
    graph: dict[str, list[str]] = defaultdict(list)  # dep → dependents
    in_degree: dict[str, int] = {s.name: 0 for s in stages}

    for stage in stages:
        for dep in stage.dependsOn:
            if dep in in_degree:
                graph[dep].append(stage.name)
                in_degree[stage.name] = in_degree.get(stage.name, 0) + 1

    queue: deque[str] = deque(name for name, d in in_degree.items() if d == 0)
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for dependent in graph[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(stages):
        # 找出环中的节点
        cycle_nodes = [name for name, d in in_degree.items() if d > 0]
        return [], f"DAG 中存在环，涉及节点: {cycle_nodes}"

    return order, None


def parallel_groups(stages: Sequence[Stage]) -> list[list[str]]:
    """将 stage 列表分组为可并行执行的"波次"。

    同一波次内的 stage 之间没有依赖关系，可以并发执行。
    """
    name_to_stage = {s.name: s for s in stages}
    in_degree: dict[str, int] = {s.name: 0 for s in stages}
    graph: dict[str, list[str]] = defaultdict(list)

    for stage in stages:
        for dep in stage.dependsOn:
            if dep in in_degree:
                graph[dep].append(stage.name)
                in_degree[stage.name] += 1

    groups: list[list[str]] = []
    ready = [name for name, d in in_degree.items() if d == 0]

    while ready:
        groups.append(sorted(ready))  # sort for determinism
        next_ready: list[str] = []
        for name in ready:
            for dep in graph[name]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_ready.append(dep)
        ready = next_ready

    return groups
