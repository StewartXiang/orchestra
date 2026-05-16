"""WorkflowState 读写 + 写隔离校验。

State 是 Workflow 内全局可读、按 Stage output.path 写隔离的 JSON 树。
每次 Stage 完成后，Workflow 调用 merge_stage_output() 将 TaskOutput 写入 State。

写隔离规则（design.md）：
  Stage 只能写 output.path 指定的子树；越权写入抛 SchemaViolation。
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.errors import SchemaViolation
from ..domain.state import ArtifactReference, StageOutput, WorkflowState
from .jsonpath_bridge import get_value, set_value

# 将 jsonpath 函数引入此模块（state 层使用 jsonpath 功能）
from ..schema.jsonpath import (
    get_value as _jget,
    set_value as _jset,
    check_write_isolation,
)


class StateStore:
    """WorkflowState 的读写管理器。

    在 Workflow 代码中使用：

        store = StateStore(initial_params=run.spec.parameters)
        store.merge_stage_output(output)
        val = store.get("$.code.patch")
    """

    def __init__(self, initial_params: dict[str, Any] | None = None) -> None:
        self._state: dict[str, Any] = {"params": initial_params or {}}
        # {stage_name -> output_path} 已声明的输出路径（用于写隔离检查）
        self._declared_outputs: dict[str, str] = {}

    def register_stage_output(self, stage_name: str, output_path: str) -> None:
        """注册 Stage 的 output_path（DAG 解析时调用，早于执行）。"""
        self._declared_outputs[stage_name] = output_path

    def merge_stage_output(self, output: StageOutput) -> None:
        """将 Stage 输出写入 State（含写隔离校验）。"""
        if not output.success or output.output_value is None:
            return

        path = output.output_path

        # 写隔离校验
        conflicts = check_write_isolation(
            output.stage_name, path, self._declared_outputs
        )
        if conflicts:
            raise SchemaViolation(
                f"State 写隔离违规: {'; '.join(conflicts)}"
            )

        _jset(self._state, path, output.output_value)

    def get(self, path: str) -> Any:
        """按 JSONPath 读取 State 值。"""
        return _jget(self._state, path)

    def snapshot(self) -> dict[str, Any]:
        """返回当前 State 的深拷贝快照（用于 continue_as_new 的 carry_over）。"""
        import copy
        return copy.deepcopy(self._state)

    def size_bytes(self) -> int:
        """序列化后的字节大小（用于监控 pipeline_state_size_bytes）。"""
        return len(json.dumps(self._state).encode())

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        declared_outputs: dict[str, str] | None = None,
    ) -> "StateStore":
        """从 continue_as_new 结转的 snapshot 恢复。"""
        store = cls()
        store._state = snapshot
        if declared_outputs:
            store._declared_outputs = declared_outputs
        return store

    @property
    def raw(self) -> dict[str, Any]:
        """直接访问内部 state dict（只读语义，勿写）。"""
        return self._state
