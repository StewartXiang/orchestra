# 架构设计：Agent Orchestra

## 本文档与其他文档关系
- `requirements.md`：要做什么、为什么
- `design.md`：怎么定义（DSL / Schema / 数据流 / CLI）
- **本文档**：怎么落地（组件 / 部署 / 数据流 / 可观测）

---

## 概述

四层架构，对标 Kubernetes 的控制面 + 数据面分离：

```
┌─────────────────────────────────────────────┐
│         配置层（YAML Schema）                 │
│  pipeline.yaml → 声明式定义流水线             │
│  可版本控制、可复制、可参数化                  │
└──────────────────┬──────────────────────────┘
                   │ 解析 + 验证 + 转换
┌──────────────────▼──────────────────────────┐
│       编排层（Temporal Server）               │
│  • Workflow DAG 调度                        │
│  • Activity 心跳监控                        │
│  • 自动重试 + 指数退避                       │
│  • 状态持久化（Event History）               │
│  • 补偿事务（Saga）                          │
│  • Signal / Update / Query（运行时交互）     │
│  • Child Workflow（子流水线）                │
│  • Schedule（定时触发）                      │
│  • Continue-As-New（长流水线截断）           │
└──────────────────┬──────────────────────────┘
                   │ Temporal SDK / MCP
┌──────────────────▼──────────────────────────┐
│       执行层（Agent Workers）                 │
│  • 9 个 Agent Profile                       │
│  • MCP 协议通信                             │
│  • LangGraph 内部流程（可选）                │
│  • 每步发心跳到 Temporal                    │
│  • Activity 幂等性 + Sandbox                │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│       可观测层（Monitoring）                  │
│  • Prometheus Metrics 导出                  │
│  • OpenTelemetry Tracing（分布式追踪）       │
│  • 流水线 Event History（审计）              │
│  • 独立审计日志（合规保留 ≥1 年）             │
│  • 结构化 JSON 日志                         │
│  • Temporal Web UI（开箱即用）               │
└─────────────────────────────────────────────┘
```

控制面 / 数据面在逻辑层，可观测性是横向贯穿能力。编排层支持 Continue-As-New 避免 Event History 超过 50K 事件上限；执行层做工具调用 Sandbox（参考 LangGraph / CrewAI）；可观测层用 OTel 串联跨 Workflow / Activity / MCP 调用链路。

### 控制面 vs 数据面

参考 K8s 设计，明确两者边界：

| 平面 | 组件 | 职责 |
|------|------|------|
| **控制面** | Temporal Server + Orchestra CLI/API | 调度决策、状态权威、审计 |
| **数据面** | Agent Workers + MCP | 实际执行任务、产生输出 |

**关键原则**：控制面只负责"决定什么时候做什么"，数据面只负责"做这件事"。两者通过 Task Queue 解耦，控制面崩溃时数据面 Activity 不会自动失败（Worker 等待重连）。

### 与同类方案的定位对比

| 维度 | LangGraph | CrewAI | AutoGen | K8s Operator | **Agent Orchestra** |
|------|-----------|--------|---------|--------------|---------------------|
| 抽象层级 | Agent 内部图 | Agent 协作角色 | Agent 多轮对话 | 资源生命周期 | **多 Agent 流水线编排** |
| 持久化 | Checkpointer | 无（内存） | 无（内存） | etcd | **Temporal Event History** |
| 故障恢复 | 弱（重启丢状态） | 弱 | 弱 | 强（reconcile） | **强（Replay）** |
| 心跳 / 超时 | ❌ | ❌ | ❌ | Probe | **Temporal 内置 5 种** |
| 长时间运行 | 不擅长 | 不擅长 | 不擅长 | 擅长 | **擅长（小时-天级）** |
| 人工审批 | 弱 | 弱 | 中 | 弱 | **强（Signal + Update）** |
| 学习曲线 | 中 | 低 | 低 | 高 | 中-高 |
| 适用场景 | 单 Agent 复杂流程 | 协作类任务 | 对话式协作 | 基础设施 | **企业级 Agent 流水线** |

**设计原则**：LangGraph 用于 Agent 内部子图，Temporal 用于 Agent 间编排。两者互补，不冲突。Agent Orchestra 在 Stage 内允许 Agent 用 LangGraph 实现内部决策图，Stage 间用 Temporal 编排。

## 为什么选 Temporal 而不是自研编排

| 能力 | 自研成本 | Temporal 内置 |
|------|---------|--------------|
| 心跳监控 | 需实现超时检测 + 重试逻辑 | `activity.heartbeat()` 一行 |
| 健康检查 | 需实现 probe + 故障转移 | Worker 自动 rebalance |
| DAG 依赖解析 | 需实现拓扑排序 + 等待机制 | `workflow.wait_condition` |
| 状态持久化 | 需数据库 + 序列化 | Event History 自动持久化 |
| 重试策略 | 需实现 backoff 算法 | `RetryPolicy` 内置 |
| 补偿事务 | 需实现 Saga 模式 | Workflow 级别补偿 |
| 超时管理 | 需定时器 + 取消逻辑 | 5 种超时粒度内置 |
| 可视化 | 需自建 UI | Temporal UI 开箱即用 |
| 生产验证 | 0 | Uber/Netflix/Stripe 万亿次 |
| Signal/Query/Update | 需实现双向通信 | 内置 |
| 子工作流 | 需实现嵌套编排 | `workflow.execute_child_workflow` |
| 工作流版本 | 需自建版本迁移 | `workflow.get_version` / patching API |
| 定时调度 | 需 cron 框架 | Temporal Schedule (cron + timezone) |
| 工作流搜索 | 需自建索引 | Temporal Visibility (Elasticsearch/SQL) |
| 多语言 SDK | 需为每语言实现 | Python/Go/Java/TS/.NET 官方 SDK |
| mTLS | 需自建 | Temporal Cloud / 自部署内置 |

**结论**：Temporal 的 80% 代码量 = 我们从头写编排层的工作量。站在巨人的肩膀上。

### Temporal 的 5 种超时（必须理解）

误用超时是 Temporal 最常见的坑，必须明确各自含义。本系统中：
- **Stage 级 4 字段**（YAML `stages[].timeouts`）：scheduleToStart / startToClose / scheduleToClose / heartbeat
- **Workflow 级 1 字段**（YAML `global.timeouts.workflowExecution`）：workflowExecution

| 超时类型 | 作用 | YAML 字段 | 推荐值 |
|---------|------|---------|--------|
| `schedule_to_start_timeout` | 任务在 Queue 中等待被 Worker 拉取的最长时间 | `stages[].timeouts.scheduleToStart` | 1m |
| `start_to_close_timeout` | Worker 拉取任务后到完成的最长时间 | `stages[].timeouts.startToClose` | Stage 实际超时（30m） |
| `schedule_to_close_timeout` | 总超时（schedule + start + retry） | `stages[].timeouts.scheduleToClose` | 通常 2-3× start_to_close |
| `heartbeat_timeout` | 两次心跳间最长间隔 | `stages[].timeouts.heartbeat` | 45s（gracePeriod） |
| `workflow_execution_timeout` | 整个 Workflow 总超时 | `global.timeouts.workflowExecution` | 全局 timeout（如 4h） |

**陷阱**：
- `heartbeat_timeout` 必须 < `start_to_close_timeout`，否则永远不生效
- `schedule_to_close_timeout` 设置后会**关闭重试**（包含所有重试时间），慎用，推荐用 `start_to_close_timeout` + RetryPolicy
- `workflow_execution_timeout` 触发后整个流水线终止，不会进入补偿；如需补偿，用 `workflow_run_timeout` + 父 Workflow 包装

### Temporal 的局限性（诚实声明）

| 局限 | 影响 | 缓解方案 |
|------|------|---------|
| Workflow Payload 限制 2MB | 大数据无法直接传递 | State 存外部引用（OSS/文件，`output.storage: reference\|oss`）|
| Event History 上限 50K events | 长流水线会被强制终止 | `continue_as_new` 截断 |
| Workflow 必须确定性 | 不能直接用 `time.now()` 等 | 用 `workflow.now()` 等 SDK API |
| SQLite 不支持高并发 | 单机起步可以，规模化必须切 PG | 容量规划阶段切换（约 50 并发 Workflow） |
| 无内置 RBAC（开源版） | 多租户需自建 | Namespace 隔离 + 网关层鉴权 |
| Worker 重启丢失内存缓存 | 进程内状态不可靠 | 所有状态走 Activity / 外部存储 |

## 四层详解

### 第一层：配置层

#### 设计原则
- **声明式 > 命令式**：描述"需要什么状态"而非"怎么做"
- **单一 YAML 文件**：一个流水线一个文件，可放进 git
- **Schema 强制**：JSON Schema 验证，CI 可集成
- **JSONPath 数据引用**：`$.stage.output` 语法传递数据
- **Spec/Status 分离**：YAML 定义 spec（期望状态），Temporal 维护 status（实际状态）
- **Pipeline / PipelineRun 分离**：定义可复用，每次执行独立审计
- **Secret 分离**：敏感信息（API Key）不放入 pipeline.yaml，使用环境变量或独立 secret 文件引用
- **配置即代码（GitOps）**：pipeline.yaml 在 git 仓库管理，CI 自动校验、自动注册到 Temporal Schedule

#### 配置结构

详见 `design.md` "顶层结构（K8s CRD 风格）"。整体改为 K8s 风格 `apiVersion/kind/metadata/spec`，便于未来扩展 CRD 化（用 K8s Operator 管理 Pipeline）。

#### 配置参数化与模板

参考 Helm/Kustomize，支持参数注入而非每个项目复制 YAML：

```yaml
# pipeline.yaml
spec:
  pipeline:
    stages:
      - name: code
        agent: "{{ params.developer }}"
        timeouts:
          startToClose: "{{ params.codeTimeout }}"

# values.yaml（每项目独立）
developer: chestnut
codeTimeout: 1h
```

提交：`orchestra submit pipeline.yaml --values values.yaml`

避免 N 个项目维护 N 份几乎相同的 pipeline.yaml。

### 第二层：编排层

#### Temporal 核心概念映射

| Temporal 概念 | Agent Orchestra 映射 |
|--------------|---------------------|
| Namespace | 项目隔离（AbyssChess / 新项目） |
| Task Queue | Agent 分组队列 |
| Workflow | 一次 PipelineRun |
| Activity | 一个 Stage 内的 Agent 任务 |
| Worker | 一个 Agent（核桃 / 栗子...） |
| heartbeat | Agent 存活性信号 |
| RetryPolicy | 重试策略（maxAttempts + backoff） |
| Signal | 异步运行时控制（cancel / pause / resume / override） |
| Update | 同步交互（approve / reject 带返回值） |
| Query | 运行时状态查询（进度、当前 Stage） |
| Child Workflow | 子流水线引用 |
| Schedule | 定时流水线触发 |
| GetVersion | Workflow 版本升级兼容 |
| Continue-As-New | 长流水线截断 Event History |
| Search Attributes | 流水线分类标签（项目、环境、优先级），可在 UI 检索 |

#### Task Queue 设计策略

Task Queue 是 Temporal 的负载均衡单位，设计上需明确：

**方案 A：每 Agent 一个 Queue**（推荐起步）
```
walnut-queue, chestnut-queue, strawberry-queue, ...
```
- ✅ 精准路由，工具白名单天然隔离
- ✅ 单 Agent 故障不影响其他
- ❌ Queue 数量多

**方案 B：按角色分 Queue**
```
developer-queue, tester-queue, ci-queue, ...
```
- ✅ 同角色 Agent 可负载均衡
- ❌ 需要额外的 Agent 选择逻辑

**方案 C：能力标签路由（Sticky + Capability）**
```
queue 按能力划分：godot-developer-queue, web-developer-queue, ...
Worker 启动时根据 labels 注册到多个 queue
```
- ✅ 支持多副本负载均衡 + 能力路由
- ✅ 容易扩缩容
- ❌ 实现复杂度高

**决策**：起步采用**方案 A**（每 Agent 独立 Queue），同角色多副本时升级为**方案 B**，规模化后切**方案 C**。

#### Workflow 生命周期（FSM）

| From | Event | To | 备注 |
|---|---|---|---|
| — | submit | Validating | YAML 解析 + Schema 校验 |
| Validating | ✅ valid | Pending | 等待 Worker 拉取 |
| Validating | ❌ invalid | Failed | validate_failed |
| Pending | Worker poll | Running | 第一个 stage 开始 |
| Running | hits approval | PendingApproval | 等待 Update |
| PendingApproval | approve Update | Running | 继续下一 stage |
| PendingApproval | reject Update / timeout(reject) | Failed → Compensating | 进入补偿 |
| PendingApproval | timeout(escalate) | PendingApproval | 升级审批人 |
| Running | Signal: pause | Paused | 运维窗口 |
| Paused | Signal: resume | Running | |
| Running | Signal: cancel | Cancelling | 走 cleanup |
| Cancelling | cleanup done | Cancelled | |
| Running | stage retries exhausted | Compensating | Saga 反向调用 |
| Compensating | compensation ok | Failed | |
| Compensating | compensation fail (onFail=alert) | Failed | 告警人工介入 |
| Running | event history > 40K | continue_as_new | 新 Workflow Run，状态结转 |
| Running | workflow timeout | Failed | 不进补偿（除非 workflow_run_timeout） |
| Running | last stage success | Succeeded | |

#### 心跳流程

```
Agent Activity 执行中
  │
  ├─ 每 15s → activity.heartbeat(details={progress, eta, current_step, checkpoint})
  │
  ├─ 45s 无心跳 → Temporal 标记 timeout
  │                → 自动重试（如果 retry > 0）
  │                → 或触发 compensation
  │
  ├─ 收到 cancellation → ActivityCancelled 异常
  │                → Agent 捕获 → 执行 cleanup → 上报最终心跳
  │
  └─ 完成 → 返回结果（Temporal 自动标记 completed）
```

心跳携带进度信息（progress 百分比、ETA、current_step），供 CLI `orchestra status --watch` 显示实时进度。心跳同时是**取消信号通道**：Agent 必须在每次心跳后检查 `activity.is_cancelled()`，及时响应取消。

#### Signal 机制（运行时交互，异步）

```
┌─────────────────────────────────────────────┐
│               Temporal Signal               │
│                                             │
│  CLI: orchestra cancel <pipeline-id>        │
│        → signal.cancel()                    │
│                                             │
│  CLI: orchestra signal <id> <name>          │
│        → signal.<name>(payload)             │
│                                             │
│  CLI: orchestra pause/resume <pipeline-id>  │
│        → signal.pause() / signal.resume()   │
└─────────────────────────────────────────────┘
```

#### Update 机制（同步交互，Temporal 1.21+）

Signal 是**异步**的（fire-and-forget），无返回值；Update 是**同步**的（带返回值 + 校验），更适合人工交互：

```
CLI: orchestra approve <stage> --reason "lgtm"
     → workflow.update.approve(reason="lgtm")
     → 在 Workflow 内校验（reason 非空、用户有权限）
     → 接受 → 返回 {approvedAt, approver}
     → 拒绝 → 抛出 ApplicationError，CLI 收到错误信息
```

适用场景：审批、参数校验、需要立即反馈的运维操作。

#### Query 机制（状态查询，只读）

```
CLI: orchestra status <pipeline-id> --details
     → query.get_progress()      → {stage: "code", progress: 45%, eta: "5min"}
     → query.get_stage_results() → {...aggregated outputs...}
     → query.get_dag_status()    → {completed: [A,B], running: [C], pending: [D]}
```

Query 必须**只读**，不能修改 Workflow 状态（Temporal 在 Replay 时会重放 Query，修改状态导致非确定性）。

#### Child Workflow（子流水线）

支持流水线引用另一个流水线作为子阶段：

```yaml
stages:
  - name: sub-pipeline
    childWorkflow:
      name: ui-test-pipeline
      version: 1.0.0
      parentClosePolicy: TERMINATE
    input: "$.code.patch"
    output: "$.ui.result"
```

子 Workflow 的 Event History 独立于父 Workflow，便于隔离和复用。

**父子关系策略**（`parentClosePolicy`）：
- `TERMINATE`（默认）：父结束时强制终止子
- `ABANDON`：父结束后子继续独立运行
- `REQUEST_CANCEL`：父结束时优雅取消子

**何时用 Child Workflow vs Activity**：
- 用 Child Workflow：独立可复用的子流水线、需要独立 Event History、生命周期可能长于父
- 用 Activity：原子任务、无内部 DAG、与父绑定

#### Continue-As-New（长流水线必备）

Event History 超过 50K 条会被 Temporal 强制终止。长流水线（如夜间批处理、监听类）必须周期性 `continue_as_new`：

```python
# 每完成 100 个 Stage 截断一次
if completed_stages >= 100:
    workflow.continue_as_new(args=[carry_over_state])
```

**约束**：
- `carry_over_state` 必须 < 2MB（Payload 限制）
- 跨 run 不保留 Activity 重试上下文
- Search Attributes 不自动继承，需手动 set

#### 脑裂防护

Temporal 天然防脑裂：Activity 心跳超时 → Server 标记超时 → Worker 收到取消指令。但如果 Agent 进程网络隔离但仍在执行，需要额外防护：

- **Fencing Token**：每次 Activity 尝试附带递增 token（Temporal 的 `attempt` 字段），Agent 拒绝过期 token
- **幂等性保证**：所有 Activity 实现为幂等操作（同一输入多次执行 = 同一结果）
- **幂等键设计**：Activity 入参中携带 `idempotency_key = workflow_id + activity_id`（不含 attempt），Agent 侧外部存储（Redis/SQLite）记录已处理键，重复请求直接返回缓存结果
- **Temporal 自动防护**：heartbeat_timeout 后 Server 保证不会有两个 Worker 同时执行同一 Activity

#### 背压控制

```
全局: maxConcurrency = 3        # 整个流水线最多 3 个并行 Stage
Agent: maxConcurrency = 1       # 单个 Agent 最多 1 个并发任务
Task Queue: rate_limit = 10/s   # Queue 级 RPS 限流（Temporal 原生支持）
Worker: max_concurrent_activities = 5  # Worker 进程级并发上限
Namespace: rate_limit = 100/s   # Namespace 级总限流
```

Temporal 原生支持 Task Queue / Namespace 级限流（`maxTaskQueueActivitiesPerSecond`），无需自己实现令牌桶。

#### Workflow 确定性约束

Workflow 代码必须是**确定性**的（Replay 时产生相同结果），否则 Replay 失败。规则：

- ❌ 不能直接调用 `time.now()` / `random.random()` / 文件 IO / 网络 IO
- ✅ 必须用 `workflow.now()` / `workflow.random()` / 在 Activity 内做副作用
- ✅ 不要在 Workflow 中维护可变全局状态
- ✅ 用 `workflow.get_version()` 标记代码变更
- ✅ 不要在 Workflow 中 import 非确定性库（如 requests / kafka-client）；Python SDK 的 `workflow_sandbox` 强制隔离

**lint 检查**：
- CI 中跑 Temporal `WorkflowReplayer` 测试，确保旧 Workflow 在新代码下能正确 Replay
- CI 中跑 `temporalio.worker.workflow_sandbox` 静态扫描，禁止非确定性 import
- 保留每周一次的生产 Event History 样本，用作回归 Replay 测试集
- Claude Code 配置了 PreToolUse hook 拦截 `import time/random/requests/aiohttp` 写入 `workflows/` 目录

详见 `CLAUDE.md` §3 与 `.claude/agents/temporal-workflow.md`。

### 第三层：执行层

#### Agent 适配器模式

每个 Agent 通过 `AgentAdapter` 统一封装：

```
AgentAdapter
├── execute_task()    → MCP 调用 Agent 执行任务（幂等 + 幂等键）
├── check_health()    → MCP 健康 probe
├── send_heartbeat()  → 更新 last_heartbeat 时间戳 + 进度
├── get_status()      → 返回 AgentStatus (IDLE/WORKING/ERROR/DEAD/CANCELLING)
├── cancel_task()     → 取消当前任务（优雅终止 + cleanup）
├── get_capabilities() → 返回 Agent 能力清单（tools/labels）
└── sandbox_exec()    → 工具调用前的沙箱校验（防越权）
```

`sandbox_exec()` 参考 CrewAI 的 tool guardrail，所有工具调用先经过白名单校验和参数 sanitize，避免 LLM 幻觉调用未声明的工具或注入恶意参数。

#### Agent 状态机

```
         ┌─────────┐
    ───→ │  IDLE   │ ←─── 任务完成 / cleanup 完成
         └────┬────┘
              │ 收到任务
         ┌────▼────┐
         │ WORKING │
         └────┬────┘
              │
    ┌─────────┼─────────────────┐
    │         │                 │
    │    ┌────▼────┐    ┌──────▼──────┐
    │    │  ERROR  │    │ CANCELLING  │
    │    └────┬────┘    └──────┬──────┘
    │         │ 重试耗尽         │ cleanup 完成
    │         ▼                 ▼
    │      回到 IDLE          回到 IDLE
    │
    └──→ DEAD（心跳超时 45s，由 Temporal 判定）
```

#### Worker 部署拓扑

```
┌─────────────────────────────────────────────────────┐
│              Temporal Server                        │
│            (SQLite / PostgreSQL)                    │
└──────────────┬──────────────────────────────────────┘
               │ gRPC
   ┌───────────┴───────────┐
   │                       │
┌──▼──────────┐    ┌──────▼─────────┐
│ Worker A    │    │ Worker B       │ ... (9 Workers)
│ (核桃)      │    │ (栗子)         │
│ poll:       │    │ poll:          │
│ walnut-queue│    │ chestnut-queue │
│ + MCP client│    │ + MCP client   │
└─────────────┘    └────────────────┘
```

每个 Agent 一个独立 Worker 进程，通过 docker compose 编排。Worker 崩溃后 Temporal 自动将任务重新分配（如果有副本）或等待重启。

**Worker 与 Agent 的进程关系**：
- **同进程模式**：Worker SDK 嵌入 Agent 进程，零网络开销
- **Sidecar 模式**（推荐起步）：Worker 独立进程通过 MCP 调 Agent，Agent 无侵入
- **Pool 模式**：一个 Worker 进程管理多个 Agent 的连接池（规模化）

起步用 Sidecar 模式（与"零侵入 Agent"原则一致），规模化时评估切换。

#### Worker 优雅关闭

Worker 进程收到 SIGTERM 时：
1. 停止从 Task Queue 拉取新任务
2. 等待当前 Activity 完成（最多 `graceful_shutdown_timeout = 60s`）
3. 超时未完成 → Activity 心跳停止 → Temporal 重试到其他 Worker
4. 进程退出

避免 K8s 滚动升级时任务被强制中断。

### 第四层：可观测层

#### Metrics 设计（Prometheus）

详见 `design.md` "关键 Metrics" 表 + `src/orchestra/observability/README.md` 命名规范。

重点新增 LLM 成本指标：Agent 流水线的核心成本是 LLM 调用，必须可观测可预算。

#### 分布式追踪（OpenTelemetry）

跨 Workflow → Activity → MCP → LLM 的完整链路：

```
Trace: pipeline-wf-abc123 (3m 24s)
├── Span: stage.code (2m 10s)
│   ├── Span: agent.walnut.execute_task
│   │   ├── Span: mcp.tool.read_file (200ms)
│   │   ├── Span: llm.deepseek.chat (45s, tokens=4200)
│   │   └── Span: mcp.tool.write_file (150ms)
│   └── Span: heartbeat (15s × 8)
└── Span: stage.test (1m 14s)
    └── ...
```

Temporal Python SDK 原生支持 OTel，传播 traceId 到 Activity；MCP 调用层手工注入 traceparent header。

#### 关键告警规则

完整规则见 `deploy/prometheus/alerts.yaml`。摘要：

```yaml
- AgentDown: agent_heartbeat_lag_seconds > 60
- PipelineHighFailureRate: rate(pipeline_runs_total{status="failed"}[5m]) / rate(pipeline_runs_total[5m]) > 0.2
- TaskQueueBacklog: task_queue_depth > 20
- ApprovalPending: approval_pending_total > 0 and time() - approval_started_at > 1800
- TemporalServerDown: up{job="temporal"} == 0
- EventHistoryNearLimit: temporal_event_history_size > 40000
- LLMCostBudgetExceeded: increase(llm_tokens_consumed_total[1h]) * cost > 50
- WorkflowReplayFailure: increase(pipeline_replay_failure_total[5m]) > 0
- StageFailureSpike: rate(stage_failure_total[5m]) > 0.1
```

#### 日志规范

```json
{
  "ts": "2026-05-16T00:00:00Z",
  "level": "info",
  "pipelineId": "wf-abc123",
  "runId": "run-xyz789",
  "stageName": "code",
  "agentName": "walnut",
  "msg": "stage started",
  "input": "$.gdd.task",
  "traceId": "trace-def456",
  "spanId": "span-ghi012"
}
```

统一 JSON 格式，每条日志包含 pipelineId + runId + stageName + traceId，可 grep / ELK 聚合，并与 OTel trace 关联。

**日志脱敏**：MCP 工具调用参数、LLM prompt/completion 中的敏感信息（API Key、个人数据）必须 redact 后再输出。提供白名单字段配置。

#### 审计追踪

每次流水线操作记录到独立审计表（schema 详见 `design.md` "审计设计"）：
- 提交人 / 触发方式（CLI / Schedule / API）
- 流水线名称 + 版本
- 各 Stage 输入 / 输出摘要（SHA256，防篡改）
- 执行时间轴（Event History 完整保留）
- 审批人 + 审批理由（Update 调用记录）
- LLM 调用记录（model + 输入 hash + 输出 hash + token 数 + cost）

## 数据流转设计

### JSONPath 数据管道

每个 Stage 声明 input/output 路径，数据在 DAG 节点间自动流转：

```yaml
stages:
  - name: code
    agent: walnut
    input: "$.gdd.task"
    output: "$.code.patch"

  - name: test
    agent: chestnut
    input: "$.code.patch"
    output: "$.test.result"
```

并行 Stage 的聚合策略详见 `design.md` "并行执行与聚合"。

### State 结构（全局共享）

```json
{
  "params": {"target_env": "prod"},
  "gdd": {"task": "...", "has_ui_change": true},
  "code": {"patch": "..."},
  "test": {"result": "pass"},
  "ui": {"result": {"strawberry": "pass", "grape": "pass"}},
  "ci": {"result": "pass"},
  "deploy": {"url": "https://..."}
}
```

**State 分片**：大数据量输出（如完整代码 diff）不存入 Temporal State（受 2MB 限制），改为存储外部引用。详见 `design.md` "State 大小与大数据策略"。

### 数据契约（Stage I/O Schema）

每个 Stage 声明输入输出 JSON Schema，运行时校验，类型不匹配立即失败。详见 `design.md` "Schema 校验"。

防止上游 Agent 输出格式漂移导致下游解析失败（LLM 输出不稳定的常见问题）。

## 健康检查 vs 心跳 vs 启动检查

| 维度 | 心跳（Liveness） | 健康检查（Readiness） | 启动检查（Startup） |
|------|-----------------|---------------------|--------------------|
| 类比 | K8s Liveness Probe | K8s Readiness Probe | K8s Startup Probe |
| 谁发起 | Agent 主动上报 | 编排层主动 probe | Worker 自检 |
| 检测什么 | Agent 进程存活 | Agent 能正常接任务 | Agent 启动完成 |
| 频率 | 15s | 30s | 仅启动时 |
| 超时 | 45s 无心跳 → DEAD | 连续 3 次失败 → UNHEALTHY | 60s 未通过 → 启动失败 |
| 动作 | 终止当前 Activity，重试 | 停止分发新任务 | 不注册到 Task Queue |
| 实现 | `activity.heartbeat()` | MCP health endpoint | Worker 启动钩子 |

**Startup Probe**：Worker 启动时先做完整 self-check（MCP 可达、依赖工具可用、模型 API 可调），通过后才注册到 Task Queue 接受任务。避免"半启动"状态接到任务后失败。

## 部署拓扑

### docker-compose 服务清单

完整定义见 `deploy/docker-compose.yml`。摘要：

| 服务 | 镜像 | 端口 | 用途 |
|---|---|---|---|
| `temporal-server` | temporalio/auto-setup:1.24 | 7233 | Workflow 执行内核（SQLite 起步） |
| `temporal-ui` | temporalio/ui:2.27 | 8080 | Web 控制台 |
| `prometheus` | prom/prometheus | 9090 | 指标采集 |
| `grafana` | grafana/grafana | 3000 | 可视化（开箱 3 个 dashboard） |
| `otel-collector` | otel/opentelemetry-collector-contrib | 4317/4318 | OTel 网关 |
| `redis` | redis:7.2-alpine | 6379 | 幂等键 / 缓存存储 |
| `worker-walnut` ... `worker-grape` | 自建 Dockerfile.worker | 9100 (metrics) | 9 个 Worker，按 PROFILE_NAME 区分 |

启动：`docker compose -f deploy/docker-compose.yml up -d`

### 容量规划与扩展路径

```
起步阶段（< 10 流水线/天）
  Temporal SQLite + 单机 docker compose
  Worker 单副本，每 Agent 一个 Queue

成长阶段（10-100 流水线/天）
  Temporal PostgreSQL + 单机部署
  Worker 单副本，关键 Agent 双副本
  Prometheus + Grafana + OTel Collector

规模阶段（> 100 流水线/天）
  Temporal Cluster (PostgreSQL + Elasticsearch + Cassandra)
  Worker 多副本 + K8s 部署 + HPA 弹性扩缩
  能力标签路由（方案 C）
  跨 Region 灾备
```

每个阶段的关键瓶颈预判：
- **SQLite 瓶颈**：>50 并发 Workflow 即出现锁等待，必须切 PG
- **单 Worker 瓶颈**：单 Agent 任务 >5 并发，Python GIL 成本明显，需多副本
- **Event History 瓶颈**：单 Workflow >10K events 性能下降，>50K 强制终止，必须 continue_as_new
- **Visibility 瓶颈**：标准 SQL Visibility 在 >100K Workflow 后查询慢，切 Elasticsearch

## 与现有 Hermes Pipeline 的关系

**Agent Orchestra 不是重写，是增强。**

```
现在：桃儿（手工编排）
         ↓ 逐步迁移
Phase 1：Temporal 替代手工（心跳/健康/重试自动化）
         ↓
Phase 2：YAML 配置替代 TASK.md（声明式）
         ↓
Phase 3：LangGraph 封装 Agent 内部（可选）
         ↓
Phase 4：可观测性完善（Prometheus + OTel + 日志 + 审计）
         ↓
Phase 5：规模化（多副本 / K8s / 多租户）
```

Agent 本身（核桃 / 栗子...）不需要改，通过 `AgentAdapter` 适配层对接 Temporal。

### 迁移 Checklist

```
□ Phase 0: 准备（1 周）
  □ 团队培训：Temporal 核心概念（Workflow/Activity/Signal/Query/Update）
  □ Replay 测试样例跑通
  □ docker compose 验证 Temporal Server 单机部署
  □ 评估当前 Agent 工具调用是否幂等（不幂等的标记并改造）

□ Phase 1: Temporal 部署（2 周）
  □ docker compose 启动 Temporal Server
  □ 注册所有 Agent Worker
  □ 验证心跳上报正常
  □ 手工测试单条流水线
  □ 单元测试 + Replay 测试 CI 集成
  □ Worker 优雅关闭测试（kill -TERM 后任务能 failover）

□ Phase 2: YAML 配置（2 周）
  □ 编写现有游戏开发流水线的 YAML 版本
  □ 验证 JSON Schema 通过
  □ parallel run: 桃儿手工 + Orchestra 自动 (对比结果)
  □ 一致性 > 95% 视为通过
  □ Stage I/O Schema 全部补齐

□ Phase 3: 切换（1 周）
  □ 选一个非关键项目试跑 3 天
  □ 确认无回归 → 切换所有流水线
  □ 旧 TASK.md 归档但不删除

□ Phase 4: 可观测（1 周）
  □ Prometheus metrics 接入 Grafana
  □ OTel Collector 部署 + Jaeger/Tempo 接入
  □ 告警规则配置
  □ 审计日志归档
  □ LLM 成本看板上线

□ Phase 5: 加固（持续）
  □ 混沌测试（kill agent / 网络分区 / Temporal Server 重启）
  □ 性能调优（heartbeat 间隔、并发上限）
  □ 文档完善（runbook / FAQ）
  □ 周期性 Replay 回归测试（用生产 Event History）

□ 回滚方案
  □ 任何时候可切回桃儿手工编排
  □ Temporal 停掉不影响 Agent 本身
  □ 旧流程文件全部保留
  □ 数据库快照支持回滚到任意时间点
```

## 关键技术决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 编排引擎 | Temporal | 内置心跳 / 重试 / 持久化 / DAG，生产验证 |
| 配置格式 | YAML（K8s 风格） | 可读性强，K8s 生态一致，未来可 CRD 化 |
| 实现语言 | Python（uv） | Temporal SDK 最成熟、LLM/MCP 生态原生、单语言降低复杂度 |
| Agent 通信 | MCP（保留现有） | 零侵入，Agent 无需改动 |
| 数据引用 | JSONPath | 标准语法，Temporal 原生支持 |
| 持久化 | Temporal SQLite → PostgreSQL | 轻量起步，规模化切换（约 50 并发 Workflow 阈值） |
| 表达式引擎 | CEL（Common Expression Language） | 沙箱安全，业界标准 |
| 指标 | Prometheus | 事实标准，Grafana 生态 |
| 追踪 | OpenTelemetry | 跨 Workflow/Activity/MCP/LLM 链路 |
| 日志 | structlog JSON | ELK / Loki 可索引 |
| 密钥 | 环境变量 + 独立文件 + Vault | 不混入 git 版本控制 |
| 幂等键存储 | Redis（推荐）/ SQLite（起步） | 持久化 + 高并发 |
| 审计存储 | SQLite（默认）/ Postgres | 与 Event History 解耦，独立保留 ≥1 年 |
| Task Queue 策略 | 每 Agent 一个 Queue → 角色 Queue → 能力 Queue | 起步精准，规模化均衡 |
| 时间表达式 | Go duration 格式（`30s`/`5m`/`1h`） | Temporal SDK 原生支持 |
| 配置热更新 | 不支持（每次提交是新版本） | 简化设计，避免运行中混乱 |
| 大对象存储 | OSS / 文件外部引用（output.storage） | 绕开 2MB Payload 限制 |
| Agent 内部流程 | LangGraph（可选） | Stage 内复杂决策图，与 Temporal 互补 |
| 多租户 | Temporal Namespace + 网关层鉴权 | 利用 Temporal 原生隔离 |
