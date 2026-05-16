# DETERMINISM REQUIRED — see CLAUDE.md §3
"""子流水线 + 动态 for_each 辅助函数。

在 PipelineWorkflow 内部调用，不单独作为 Workflow 入口（除非需要独立 History）。

⚠️ 本文件内所有代码必须满足确定性约束：
  - 不能调用 time.now() / random / IO
  - 所有副作用通过 Activity 隔离
  - 使用 workflow.now() / workflow.random()
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class ChildPipelineWorkflow:
    """子流水线 Workflow（独立 Event History）。

    由父 PipelineWorkflow 通过 execute_child_workflow 调用。
    参数：父流水线传入 TaskInput，本 Workflow 执行后返回输出。
    """

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        # 子流水线内部逻辑复用 PipelineWorkflow 框架
        # 实际在运行时动态注册，这里仅作占位
        return {"status": "child_completed", "params": params}
