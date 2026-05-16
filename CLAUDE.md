# Agent Orchestra — 项目宪法

> 本文件是项目的"单一事实源"指针。任何代码、配置、决策都应能在 `docs/{requirements,design,architecture}.md` 找到根据；冲突时**以三份文档为准**，再回写本文件。

## 1. 项目本质

Agent Orchestra 是一个 **AI Agent 流水线编排引擎**：用 Temporal 做执行内核，用自研 YAML DSL 做声明式拓扑，用 MCP 做 Agent 通信，让多个 LLM Agent 像 Kubernetes 管理 Pod 一样被调度、监控、重试和补偿。

- **要做的**：多 Agent 编排、心跳、健康检查、DAG 调度、重试、补偿、审批、动态生成、审计、可观测
- **不做的**：通用工作流平台、LLM 推理网关、Web 编辑器、跨地域多活、替换 MCP（详见 `docs/requirements.md` 显式非目标）

## 2. 领域术语（强制统一）

写代码、写文档、写注释时必须使用以下词汇：

| 概念 | 词汇 | 不要写成 |
|---|---|---|
| 业务流程定义 | **Pipeline** | workflow definition / job spec |
| 流程的一次执行 | **PipelineRun** | run / instance（除非引用 Temporal 内部） |
| 流程中的一步 | **Stage**（或同义 Node） | step / task |
| 持久化执行实例 | **Workflow**（Temporal 内部） | — |
| Workflow 调用的原子操作 | **Activity** | task / job |
| LLM 实例 | **Agent** | LLM / bot |
| 运行 Workflow/Activity 的进程 | **Worker** | runner |
| Agent 的物理身份 | **Profile**（核桃/杏仁等） | persona |
| Agent 的逻辑角色 | **Role**（developer/tester...） | type |
| Agent 的能力标签 | **Capability**（python/godot...） | skill |
| 流水线产物 | **Artifact** | output（除非指 JSONPath 数据） |
| 异步外部事件 | **Signal** | message |
| 同步带返回值的指令 | **Update**（Temporal 1.21+） | RPC |
| 失败补偿事务 | **Saga** | rollback |

## 3. 确定性铁律（Workflow 代码）

`src/orchestra/workflows/**` 下的所有代码必须满足 Temporal 确定性约束。**违反任何一条都会导致 Replay 失败**：

| ❌ 禁用 | ✅ 替代 |
|---|---|
| `time.now()` / `datetime.now()` | `workflow.now()` |
| `time.sleep()` / `asyncio.sleep()` | `await workflow.sleep(...)` 或 `await workflow.wait_condition(...)` |
| `random.random()` / `uuid.uuid4()` | `workflow.random()` / `workflow.uuid4()` |
| 文件 IO / HTTP / DB 调用 | 包成 Activity |
| `import requests` / `aiohttp` / 任何 IO 库 | Workflow 内严禁；Activity 中允许 |
| 全局可变状态 / 单例 | Workflow 实例字段 |
| 直接读环境变量 | Activity 读后传入 |
| 多线程 / 子进程 | 单协程内 await |

强制开启 `temporalio.worker.workflow_sandbox`。每个 Workflow 文件首行注释 `# DETERMINISM REQUIRED — see CLAUDE.md §3`。

## 4. 幂等铁律（Activity 代码）

`src/orchestra/activities/**` 下每个 Activity **必须**：

```python
@activity.defn
async def my_activity(input: ActivityInput) -> ActivityOutput:
    # 1. 心跳（第一行）—— 报告活着 + 携带进度
    activity.heartbeat({"phase": "started", "progress": 0})

    # 2. 幂等键查询
    info = activity.info()
    key = f"{info.workflow_id}/{info.activity_id}/{info.attempt}"
    if cached := await idempotency.get(key):
        return cached

    # 3. 周期性心跳 + 取消检查
    while not done:
        if activity.is_cancelled():
            await cleanup()
            raise ActivityCancellationError()
        activity.heartbeat({"phase": "running", "progress": pct})
        ...

    # 4. 写幂等结果
    await idempotency.put(key, result, ttl="24h")
    return result
```

不写心跳 = 长任务必死；不查幂等 = 副作用重复执行；不响应取消 = 优雅关闭失效。三者缺一不可。

## 5. 代码生成约定

- **单文件 ≤ 400 行**，超出必须拆分
- **类型注解强制**（`from __future__ import annotations` + 严格 mypy）
- **Public API 必须 docstring**，docstring 引用 `docs/design.md` 或 `docs/architecture.md` 章节锚点（如 `参见 design.md §"DAG 设计"`）
- **不写 README 之外的解释性 markdown**（设计变更回写到 docs/）
- **import 顺序**：stdlib → 第三方 → `orchestra.domain` → `orchestra.<其他子包>`；`workflows/` 不得 import `activities/` / `adapters/` / `state/` / `observability/` 的副作用代码
- **错误类型**继承 `orchestra.domain.errors.OrchestraError`；标记 `is_retryable: bool` 类属性
- **常量与魔术字符串**集中在 `orchestra.domain.enums`

## 6. 测试要求

| 新增内容 | 必须配套测试 |
|---|---|
| Workflow | `tests/integration/test_workflow_<name>.py` happy path + `tests/replay/fixtures/<name>.json` |
| Activity | `tests/unit/` mock 适配器测试（覆盖正常 + 取消 + 重试 + 幂等命中） |
| Schema 字段 | `tests/unit/test_validator.py` 用例（合法 + 非法各 1） |
| Adapter | `adapters/mock.py` 同步更新；契约测试通过 |
| Metric | `observability` agent 校验命名规范 |

CI 强制项：`ruff check` / `mypy --strict` / `pytest tests/unit tests/replay` 全绿。

## 7. 提交流程

- Commit message 格式：`<area>: <imperative>`，area ∈ `{schema, workflow, activity, adapter, state, obs, cli, deploy, docs, test}`
- PR 描述必须答 3 题：① 改了什么 ② 为何这样 ③ 如何验证
- 涉及 Workflow 改动：必须勾选"已跑 replay 测试"
- 涉及 Schema 改动：必须勾选"已 bump apiVersion 或保持兼容"

## 8. 子 Agent 调度

为高频陷阱配置了特化 subagent。当任务命中以下场景时，**使用对应 subagent 而非通用 agent**：

| 场景 | 用 |
|---|---|
| 写/审 Workflow | `temporal-workflow` |
| 写/审 Activity | `temporal-activity` |
| 改 Schema / DSL / DAG 校验 | `schema-keeper` |
| Workflow 改后做兼容性回归 | `replay-guardian` |
| 实现/扩展 AgentAdapter | `mcp-adapter` |
| 新增 metric/span/audit 字段 | `observability` |

详见 `.claude/agents/*.md`。

## 9. 文档地位

- `docs/requirements.md` — 做什么、为什么（产品视角）
- `docs/design.md` — 怎么定义（DSL/Schema/数据流/CLI）
- `docs/architecture.md` — 怎么落地（组件/部署/可观测）
- `runbook/*.md` — 故障处置 SOP

代码与文档冲突时**以文档为准**；修代码同时要修文档（同 PR）。
