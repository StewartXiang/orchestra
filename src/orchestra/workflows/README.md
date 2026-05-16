# workflows/

> ⚠️ **DETERMINISM REQUIRED** — 本目录所有代码必须满足 Temporal 确定性约束。
> 修改前必读 `CLAUDE.md` §3，并使用 `temporal-workflow` 子 agent。

## 职责
Temporal Workflow 定义。**只描述决策**，不做副作用。所有 IO 必须通过 `await workflow.execute_activity(...)` 包装。

## 关键文件

| 文件 | 责任 |
|---|---|
| `pipeline_workflow.py` | `PipelineWorkflow` 主体：消费 Pipeline 对象 → 解析 DAG → 派发 Activity → 聚合输出 |
| `child_workflows.py` | 子流水线引用 + dynamic for_each（execute_child_workflow） |
| `signals.py` | `cancel` / `approve` / `reject` / `pause` / `resume` / `override` |
| `queries.py` | `get_progress` / `get_dag_status` / `get_approval_status` / `get_state_size` |
| `updates.py` | Update API：带返回值的同步交互（审批校验返回审批信息） |

## 强制约定

1. **首行注释**：`# DETERMINISM REQUIRED — see CLAUDE.md §3`
2. 时间用 `workflow.now()` / `workflow.sleep()`；不要 `time.*` / `datetime.now()`
3. 随机用 `workflow.random()` / `workflow.uuid4()`；不要 `random` / `uuid`
4. 副作用走 Activity；不要直接 IO
5. 长流水线（>100 stage 或 >10K event）必须 `workflow.continue_as_new(...)`
6. Workflow 类字段保存唯一可变状态；禁全局可变变量
7. 所有 Query 方法必须只读
8. 修改已上线 Workflow → 用 `workflow.get_version("change-name", DEFAULT_VERSION, 1)` 标记

## 信号 / 查询 / 更新 表

| 名称 | 类型 | 用途 |
|---|---|---|
| `cancel` | signal | 取消流水线（触发 cleanup） |
| `pause` / `resume` | signal | 运维窗口暂停 |
| `approve` / `reject` | update | 审批节点（带返回值确认） |
| `override` | signal | 运行时覆盖参数 |
| `get_progress` | query | 当前 stage / 百分比 / ETA |
| `get_dag_status` | query | 已完成 / 运行中 / 待执行 stage 列表 |
| `get_approval_status` | query | 待审批 stage 详情 |

## 边界
- 不 import `activities/` / `adapters/` / `state/` / `observability/` 的副作用代码
- 仅允许 import `domain/` 和 `temporalio.workflow`
- 启用 SDK `workflow_sandbox`

## 测试策略
- `tests/integration/test_workflow_*.py`：每种 DAG 模式 happy path
- `tests/replay/`：每个 Workflow 至少 1 条 history fixture，CI 强制 `WorkflowReplayer` 通过
- 改 Workflow 后必跑 `replay-guardian`

## 常见陷阱
- 在 Workflow 里 `if some_dict.get("key")`：dict 顺序非确定 → 用 `sorted(dict.items())`
- `await asyncio.gather(...)` 顺序敏感 → 改用 `workflow.wait_condition` 或显式 task 列表
- Query 里 `self.counter += 1` → 违反只读
- `import requests` → SDK sandbox 直接拒绝
