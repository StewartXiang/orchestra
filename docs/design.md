# 设计文档：配置 Schema 与流水线 DSL

## 本文档与其他文档关系
- `requirements.md`：要做什么、为什么
- **本文档**：怎么定义（DSL / Schema / 数据流 / CLI）
- `architecture.md`：怎么落地（组件 / 部署 / 可观测）

---

## 设计目标

提供一套声明式的 YAML DSL，描述 AI Agent 流水线的全局拓扑、数据流转和运维策略。借鉴 Argo Workflows / Tekton（CRD 风格）、Temporal（持久化执行）、LangGraph（状态机 + 检查点）、CrewAI（Agent 协作语义），同时针对 AI Agent 场景（长尾延迟、不确定输出、人工介入）做特化。

### 设计原则

1. **声明式优于命令式**：描述状态，不描述过程
2. **渐进复杂度**：最简单的流水线只需要 `name` + `stages`；复杂能力（审批 / 动态 / 补偿）按需开启
3. **K8s 一致性**：术语和结构对齐 Kubernetes（apiVersion / kind / metadata / spec / status）
4. **自文档化**：配置本身就是文档
5. **Spec / Status 分离**：YAML 定义期望（Spec），运行时状态（Status）由 Temporal 管理且可 Query 查询
6. **Pipeline / PipelineRun 分离**：定义可版本化复用，每次执行产生独立 Run（参考 Tekton）
7. **幂等安全**：所有 Task 级操作可安全重放，关键 Activity 实现幂等
8. **失败优先**：Schema 严格校验，未声明字段拒绝；DAG 静态分析（环检测、孤儿节点、未引用 agent）
9. **确定性优先（Determinism First）**：Workflow 代码必须满足 Temporal 确定性约束，所有副作用通过 Activity 隔离
10. **演进兼容（Versioning）**：DSL Schema 与 Workflow 代码均需考虑向后兼容，使用 `apiVersion` + `workflow.get_version()` 双机制
11. **可观测优于日志**：每个 Stage 必须可被 Query / Signal / Metrics 三种方式观测

---

## 资源类型

| Kind | 用途 | 文件 |
|---|---|---|
| `Pipeline` | 流水线**定义**，描述 DAG 拓扑 | `examples/*.pipeline.yaml` |
| `PipelineRun` | 流水线一次**执行实例**，引用 Pipeline + 提供运行时参数 | 由 CLI 生成或 Webhook 触发 |
| `AgentProfileSet` | Agent profile 集群配置 | `config/profiles.yaml` |
| `CapabilityRegistry` | capability / role 词表 | `config/capabilities.yaml` |

JSON Schema 见 `schema/{pipeline,pipeline-run,agent-profile}.schema.json`，遵循 JSON Schema Draft 2020-12（原生支持 `unevaluatedProperties` 严格模式）。

---

## 顶层结构（K8s CRD 风格）

```yaml
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: game-dev-pipeline               # 必填，DNS-1123，与 Temporal Workflow ID 兼容
  namespace: abyss-chess                # 必填，对应 Temporal Namespace
  version: 1.2.0                        # SemVer
  labels:                               # K8s 风格标签
    project: abyss-chess
    env: prod
  annotations:                          # 非语义元数据
    owner: "taoer@example.com"
    docs: "https://wiki/pipelines/game-dev"
spec:
  description: "..."
  agents: { ... }
  pipeline: { ... }
  secrets: [ ... ]
  global: { ... }
  parameters: { ... }
status:                                 # 只读，由引擎维护
  phase: Running                        # Pending | Running | Paused | Succeeded | Failed | Cancelled | Compensating
  workflowId: "wf-abc123"
  runId: "run-xyz789"
  startedAt: "..."
  stages: [ ... ]
```

PipelineRun 引用 Pipeline 提供运行时参数：

```yaml
apiVersion: orchestra.io/v1
kind: PipelineRun
metadata:
  name: game-dev-2026-05-16-001
  namespace: abyss-chess
spec:
  pipelineRef:
    name: game-dev-pipeline
    version: 1.2.0
  parameters:
    gdd: "/docs/gdd/v3.md"
  trigger:
    kind: manual                        # manual | schedule | webhook | signal | api
    actor: "taoer"
  priority: high
  idempotencyKey: "release-2026-05-16"
```

---

## 核心 Schema 定义

### AgentSpec — Agent 规格

```yaml
spec:
  agents:
    walnut:
      role: developer                    # 角色名（语义标签）
      model: deepseek-v4-pro             # LLM 模型
      tools:                             # 工具白名单（强制约束）
        - godot_edit
        - git_push
      labels:                            # Agent 标签
        team: core
      capabilities: [python, godot]      # 能力标签
      taskQueue: "agent-walnut"          # 显式 Temporal Task Queue 名（默认 = agent name）
      mcpEndpoint: "mcp://localhost:18761"
      maxConcurrency: 2                  # 单个 Agent 最大并发任务数

      # 资源声明（用于调度与限流，对标 K8s ResourceRequirements）
      resources:
        requests:
          memory: "2Gi"
          cpu: "500m"
        limits:
          memory: "4Gi"
          cpu: "2000m"
          tokensPerMinute: 100000        # LLM Token 速率限制（令牌桶）

      # 三级探针（对齐 K8s Pod Probe 语义）
      startupProbe:                      # 启动检查（仅启动期，通过后切换到 readiness）
        endpoint: "mcp://localhost:18761/health"
        initialDelay: 10s
        periodSeconds: 5s
        failureThreshold: 6              # 30s 内必须就绪
      readinessProbe:                    # 就绪检查（决定是否分发任务）
        endpoint: "mcp://localhost:18761/health"
        periodSeconds: 30s
        timeout: 5s
        failureThreshold: 3
      livenessProbe:                     # 存活检查（基于心跳，决定是否重启）
        heartbeatInterval: 15s
        gracePeriod: 45s

      retry:                             # 任务重试默认策略
        maxAttempts: 3
        backoff: exponential
        initialInterval: 10s
        maxInterval: 5m
        coefficient: 2.0                 # 指数退避系数
        nonRetryableErrors:              # 永久性错误不重试
          - AuthError
          - ToolNotAllowed
          - InvalidInput
          - SchemaViolation
          - ApprovalRejected
          - BudgetExceeded
```

`tokensPerMinute` 对 AI Agent 至关重要——LLM 配额是稀缺资源，Worker 侧需在派发任务前做令牌桶限流，避免触发上游 429。

### PipelineSpec — 流水线规格

```yaml
spec:
  pipeline:
    stages:
      - name: code                       # 节点名称（流水线内唯一，DNS-1123）
        # ----- 执行体（七选一）-----
        agent: walnut                    # (1) 单 agent
        agents: [s1, s2]                 # (2) 并行 agents
        agentSelector:                   # (3) 按能力路由（与 agent / agents 互斥或组合用作过滤）
          role: developer
          capabilities: [godot]
        childWorkflow:                   # (4) 子流水线
          name: code-review-sub
          version: 1.2.0
          parentClosePolicy: TERMINATE
        approval:                        # (5) 人工审批节点
          approvers: ["ou_xxx"]
          policy: any
          timeout: 1h
        dynamic:                         # (6) 动态 for_each
          generator: for_each
          ...
        loop:                            # (7) 受限循环
          body: [test, fix]
          ...

        # ----- 调度 -----
        dependsOn: [design-review]       # DAG 依赖（前驱节点列表）
        condition: 'test.result == "pass"'
        priority: 50                     # 0-100，影响 Task Queue 出队顺序

        # ----- 数据 -----
        input: "$.gdd.task"              # JSONPath 输入
        output:                          # JSONPath 输出 + 存储策略
          path: "$.code.patch"
          storage: reference             # inline | reference | oss
          bucket: "orchestra-artifacts"  # 仅 oss
          ttl: 30d
        inputSchema: { ... }             # 输入数据契约
        outputSchema: { ... }            # 输出数据契约
        schemaViolationPolicy: fail      # fail | warn
        requireUpstream: false           # 上游 SKIPPED 时本 stage 是否级联跳过

        # ----- 超时（4 字段，与 Temporal Activity Options 一一对应）-----
        timeouts:
          scheduleToStart: 1m            # 派发到开始：队列等待
          startToClose: 30m              # 单次执行最长
          scheduleToClose: 2h            # 包含所有重试的总超时
          heartbeat: 30s                 # 心跳间隔（覆盖 Agent 默认）

        # ----- 重试 -----
        retry:                           # Stage 级覆盖 Agent 级
          maxAttempts: 3
          backoff: exponential

        # ----- 并行 / 失败语义 -----
        aggregateStrategy: all           # all | any | first | merge | vote | quorum
        quorumThreshold: 0.66            # 仅 quorum
        onFailure: fail                  # continue | fail | compensate

        # ----- 产出物 -----
        artifacts:
          - name: build
            path: /opt/build/
            type: directory
            retention: 7d
            compress: true
            storageClass: local          # local | s3 | oss
            hash: sha256

        # ----- 缓存（对标 Argo Memoize / GitHub Actions cache）-----
        cache:
          key: "{{ inputs.gdd.task | sha256 }}"
          ttl: 24h
          enabled: true

        # ----- 副作用与幂等 -----
        idempotencyKey: "{{ pipelineId }}-{{ stageName }}"
        sideEffects: [git, fs]           # 声明可能的副作用：git/fs/deploy/network/db

    # ----- Saga 补偿 -----
    compensation:
      strategy: reverse                  # reverse(逆序) | parallel | custom
      maxCompensationAttempts: 2
      onCompensationFailure: alert       # alert | abort
      actions:
        - forStage: deploy
          agent: coconut
          action: rollback
          runOn: any_failure             # any_failure | specific_stage

    # ----- Schedule 调度 -----
    schedule: "0 9 * * 1-5"              # 5 字段 cron（本地时区）
```

**超时字段为 4 字段对象**（不是单个 `timeout`）：与 Temporal Activity Options 一一对应。这是 Temporal 用户的常见踩坑点——`timeout` 单字段歧义大（到底是 startToClose 还是 scheduleToClose？）。

**`cache` 字段**：AI Agent Stage 调用 LLM 成本高，相同输入应允许复用结果。引擎实现 Key→Result 的缓存层（Redis / 文件）。

**`sideEffects` 声明**：明确 Stage 是否触碰外部系统（git push / 部署），决定是否安全重跑。`re-run --from` 命令会基于此提示风险。

### GlobalSpec — 全局策略

```yaml
spec:
  global:
    heartbeatInterval: 15s               # 默认心跳上报频率
    maxConcurrency: 3                    # 全流水线最大并行度
    timeouts:
      workflowExecution: 4h              # Workflow 总超时（替代 Temporal workflow_execution_timeout）
      activityDefault: 30m               # Activity 默认 startToClose
    notification:
      channels: [feishu, email]
      target: "ou_xxx"
      onEvents: [failed, approval_pending, succeeded, started]
    dryRun: false
    artifactsBasePath: "/opt/agent-orchestra/artifacts"
    priority: high                       # low | normal | high | critical
    retention:
      historyDays: 30                    # Event History 保留
      artifactsDays: 7                   # 产出物保留
      successfulRunsKeep: 100            # 成功运行保留数
      failedRunsKeep: 50
```

### SecretRef — 密钥引用

```yaml
spec:
  secrets:
    - name: llm-api-key
      fromEnv: LLM_API_KEY
    - name: deploy-token
      fromFile: /etc/orchestra/secrets/deploy-token
    - name: db-password
      fromVault:
        path: secret/data/orchestra/db
        key: password
```

密钥不写入 pipeline.yaml。引擎层在 Activity 执行前注入到 Agent，Agent 通过 `${secrets.llm-api-key}` 占位符引用。

**Temporal Custom PayloadCodec 加密**：含密钥的 Activity input/output 必须通过 Custom PayloadCodec 加密（AES-256-GCM），密钥派生自 KMS。Temporal Web UI 显示密文，仅授权 CLI（持有 KMS 权限）能解密。

### Parameters — 运行时参数

对标 Argo `arguments.parameters`，避免为相似流水线维护多份 YAML：

```yaml
spec:
  parameters:
    - name: target_env
      type: string
      default: staging
      enum: [dev, staging, prod]
      description: "部署目标环境"
    - name: skip_tests
      type: boolean
      default: false
```

提交时注入：
```bash
orchestra submit pipeline.yaml --param target_env=prod --param skip_tests=false
```

模板内通过 `{{ params.target_env }}` 引用。

---

## DAG 设计

### 依赖解析算法

使用 Kahn 算法（拓扑排序）检测环：

```
1. 构建邻接表 (dep → stage)
2. 计算入度
3. BFS 拓扑排序
4. visited != total_nodes → 存在环
```

### 静态校验清单

提交时执行的全部静态检查（`orchestra validate` / `orchestra submit` 均执行）：

```
✓ JSON Schema 校验（含 unevaluatedProperties: false 严格模式）
✓ apiVersion 兼容性检查（新版引擎拒绝过旧 / 不支持的 apiVersion）
✓ DAG 环检测（Kahn 拓扑排序）
✓ 孤儿节点检测（无依赖且无被依赖且非起点）
✓ Agent 引用完整性（stage.agent 必须在 agents 中定义；agentSelector 必须能匹配到至少一个 profile）
✓ JSONPath 数据流校验（input 必须有上游 stage 写入）
✓ 工具白名单验证（stage 隐含调用的工具是否在 agent.tools 中）
✓ 密钥引用完整性（${secrets.xxx} 必须在 secrets 中定义）
✓ 超时合理性（heartbeat < startToClose < scheduleToClose < global.timeouts.workflowExecution）
✓ 子流水线递归检测（A → B → A 死循环）
✓ 资源配额校验（sum(agent.resources.requests) ≤ 集群容量）
✓ 参数占位符完整性（{{ params.xxx }} 必须在 parameters 中声明）
✓ 命名规范（DNS-1123：小写字母、数字、连字符，≤63 字符）
✓ 补偿动作引用完整性（compensation.actions[].forStage 必须存在）
✓ capability 词表完整性（所有 capability 必须在 config/capabilities.yaml 内）
```

### 支持的 DAG 模式

```
1. 线性链：A → B → C
2. 扇出（并行）：A → {B, C, D}
3. 扇入（汇聚）：{A, B, C} → D
4. 条件分支：A → test → {pass→deploy, fail→fix}
5. 混合：design → code → test → {pass→deploy, fail→fix→test, skip→manual}
6. 人工审批：code → approval → deploy（Signal / Update 驱动）
7. 动态子图：diagnose → for_each(bugs) → fix → validate → deploy
8. Map-Reduce：split → [worker1..N] → aggregate
9. 子流水线：通过 childWorkflow 引用，支持递归（编译期检测死循环）
10. 迭代 / 循环：while condition do stage（受限循环，最大迭代数强制声明）
```

### 迭代节点（受限循环）

AI Agent 场景常见 "test → fix → test → fix" 循环，对标 LangGraph 的循环边：

```yaml
- name: test-fix-loop
  loop:
    body: [test, fix]                    # 引用 stage 名
    condition: 'test.result != "pass"'   # 继续循环条件
    maxIterations: 5                     # 强制上限（防死循环）
    onMaxReached: fail                   # fail | continue
```

引擎将其展开为 Temporal Workflow 的 `while` 循环，每次迭代独立计数。

### 并行执行与聚合

`agents: [strawberry, grape]` → 并行执行，结果按策略合并：

| 策略 | 行为 | 适用场景 |
|------|------|---------|
| `all` (默认) | 全部成功才算成功 | 多视角验证（UI 测试） |
| `any` | 任意一个成功即可 | 多模型兜底 |
| `first` | 第一个完成的为准，其余取消 | 竞速模式 |
| `merge` | 合并所有输出（按 schema） | 多 Agent 生成不同部分 |
| `vote` | 多数投票（n/2+1） | 一致性检查 |
| `quorum` | 自定义阈值（如 2/3，由 `quorumThreshold` 指定） | 弱一致性容忍 |

**`first` 策略的取消语义**：第一个完成后，其余 Activity 必须收到 cancellation signal 并优雅退出（释放 LLM 配额），通过 Temporal `CancellationScope` 实现。

### 条件执行与跳过语义

`condition: "test.result == 'pass'"` → 沙箱表达式求值。

支持的运算符：`==` `!=` `<` `>` `<=` `>=` `and` `or` `not` `in` `matches`(正则) `size()`

**表达式引擎实现**：使用 [CEL (Common Expression Language)](https://github.com/google/cel-spec)，禁止任意代码执行。对标 K8s Admission Webhook、Argo `when`。

```yaml
condition: 'test.result == "pass" && size(diagnosis.bugs) < 10'
```

**跳过语义**：

| condition 求值 | 结果 |
|---------------|------|
| `True` | 执行 stage |
| `False` | 跳过 stage（标记 SKIPPED，下游视为已完成不阻塞） |
| 抛异常 | 失败 stage（触发 retry / compensation） |

**下游传播策略**：当上游 SKIPPED 时，下游 input 中对应字段为 `null`，stage 可声明 `requireUpstream: true` 强制要求非 null（否则该 stage 也 SKIPPED 级联）。

### 人工审批节点（Human-in-the-Loop）

对标 LangGraph `interrupt` 机制 + Temporal Update：

```yaml
- name: deploy
  dependsOn: [ci-gate]
  approval:
    approvers:                           # 多人审批
      - ou_xxx
      - ou_yyy
    policy: any                          # any | all | quorum
    quorumCount: 2                       # 仅 quorum
    message: "CI 通过，是否部署到生产？"
    timeout: 1h
    onTimeout: reject                    # reject | approve | escalate
    escalateTo: ou_manager
    reminderInterval: 15m
    contextFields:                       # 通知卡片中展示的 State 字段
      - "$.ci.report"
      - "$.test.coverage"
```

**Update 优于 Signal 的场景**：审批是同步交互（需要立即知道是否被接受）。Update 在 Workflow 内可校验后返回结果或拒绝，CLI 收到错误信息更友好。Signal 仍保留作为"通知 Workflow 有新事件"的轻量通道。

CLI：
```bash
orchestra approve <pipeline-id> deploy --as ou_xxx
orchestra reject  <pipeline-id> deploy --reason "不稳定" --as ou_xxx
orchestra status  <pipeline-id> --pending-approvals
```

### 动态 DAG（运行时生成）

```yaml
- name: fix-each
  dynamic:
    generator: for_each
    input: "$.diagnosis.bugs"
    template:
      name: "fix-bug-{{ item.id }}"
      agent: walnut
      input: "$.item"
      output: "$.fixes[{{ item.id }}]"
      timeouts: {startToClose: 10m, heartbeat: 30s}
    maxParallel: 3
    maxItems: 1000                       # 防止 Event History 膨胀
    onItemFailure: continue              # continue | fail_fast
    aggregateOutput: "$.fixes"           # 显式聚合目标
```

**实现注意**：动态生成的 stage 数量必须在单次 Workflow 内确定（Temporal 确定性约束），可通过 SideEffect 或独立 Activity 计算后传入。生成数量上限通过 `maxItems` 强制（建议 ≤1000）。

引擎内部用 `execute_child_workflow` 隔离每个 item 的 History（推荐），避免父 Workflow Event History 膨胀。

---

## 数据流转设计

### JSONPath 状态管道

数据在整个 Workflow 的全局 State 中流转：

```python
workflow_state = {
    "params": { ... },                   # 运行时参数（只读）
    "gdd": {"task": "...", "has_ui_change": True},
    "code": {"patch": "..."},
    "test": {"result": "pass"},
    # ...
}
```

每个 Stage：读 input → Agent 处理 → 写 output。

**State 写隔离**：每个 Stage 只能写入自己的 `output.path` 路径，不能修改其他 Stage 的输出。引擎在 Stage 完成时做路径校验（防止 Agent 越权污染状态），违规抛 `SchemaViolation`。

### State 大小与大数据策略

Temporal 单条 Event 默认 2MB 上限，整个 History 50MB 上限。策略：

```yaml
output:
  path: "$.code.patch"
  storage: auto                          # inline | reference | oss
```

| storage | 行为 |
|------|------|
| `inline` | 直接存 State |
| `reference` | 存文件路径，State 仅存指针 `{"path":"...", "sha256":"...", "size":N}` |
| `oss` | 上传对象存储（需配 `bucket` / `ttl`），State 存 OSS URL + meta |

默认 inline；> 100KB 由引擎自动改为 reference。

**写入预检**：Activity 返回前先序列化测大小，>2MB 时强制改 reference 并 warning（避免 Temporal Server 拒绝整个 Event）。

**State 总大小监控**：每次 Stage 完成后采样 `len(json.dumps(state))`，导出为 metric `pipeline_state_size_bytes`，超过 10MB 告警。

### 产出物管理（Artifacts）

```yaml
- name: code
  artifacts:
    - name: game_build
      path: /opt/godot/projects/AbyssChess/build/
      type: directory                    # file | directory
      retention: 7d
      compress: true
      storageClass: local                # local | s3 | oss
      hash: sha256                       # 写入时计算校验和
```

目录结构：
```
/opt/agent-orchestra/artifacts/
└── <namespace>/<pipeline-name>/<run-id>/<stage>/<artifact>/
    ├── data.tar.gz
    └── manifest.json   ← {hash, size, createdAt, retention}
```

**跨 Stage 引用**：下游 Stage 可声明 `inputArtifacts: [{from: "code/game_build"}]`，引擎在执行前自动挂载 / 下载。

### Schema 校验

```yaml
- name: code
  inputSchema:
    type: object
    required: [task]
  outputSchema:
    type: object
    required: [patch]
  schemaViolationPolicy: fail            # fail | warn
```

Schema 不通过时的行为：默认 `fail`（视为 Stage 失败触发重试），可改为 `warn` 仅记录。

---

## 心跳与健康检查设计

### 三级探针 + 心跳

```
Agent 进程
│
├── Liveness（心跳）: Activity 主动上报到 Temporal Server
│   ├── 间隔: heartbeatInterval (默认 15s, 推荐取 timeouts.heartbeat / 3)
│   ├── 方式: activity.heartbeat(progress)
│   ├── 超时: gracePeriod (45s) 无心跳 → ActivityCancelled
│   └── 动作: 重试 or 补偿
│
├── Readiness: 编排层主动 Probe
│   ├── 间隔: 30s (readinessProbe.periodSeconds)
│   ├── 方式: MCP health endpoint
│   ├── 阈值: failureThreshold 次失败 → 标记 NotReady
│   └── 动作: 不再分发新任务（已运行的不中断）
│
└── Startup: Worker 启动时一次性自检
    ├── 检查: MCP 可达 + 工具可用 + 模型 API 可调
    ├── 通过: 注册到 Task Queue
    └── 失败: 不接任务，返回错误状态
```

### 心跳实现（携带进度）

```python
@activity.defn
async def execute_agent_task(input: PipelineInput) -> dict:
    info = activity.info()

    # 从心跳详情恢复（重试时）
    heartbeat_details = info.heartbeat_details
    resume_from = heartbeat_details[0] if heartbeat_details else None

    activity.heartbeat({
        "stage": input.stage_name,
        "phase": "started",
        "progress": resume_from.get("progress", 0) if resume_from else 0,
        "attempt": info.attempt,
    })

    async def on_progress(pct, eta, checkpoint=None):
        activity.heartbeat({
            "stage": input.stage_name,
            "phase": "running",
            "progress": pct,
            "eta": eta,
            "checkpoint": checkpoint,    # 长任务断点续传
        })

    result = await call_mcp(input, on_progress=on_progress, resume_from=resume_from)
    return result
```

**心跳详情用于断点续传**：长任务（如 build 30min）失败重试时，从最后心跳的 checkpoint 恢复，不必从零开始——这是 Temporal 心跳机制的高级用法。

### 健康检查与 Task Queue 联动

Agent 标记 NotReady 时，Worker 进程应停止 polling 对应 Task Queue（通过 `worker.suspend_polling()`），让任务路由到副本 Worker 而非积压。

---

## 重试策略设计

### 三级重试（优先级递减）

```
Stage retry > Agent retry > Global default
```

### 退避算法（对齐 Temporal RetryPolicy）

```
Fixed:        initialInterval, initialInterval, ...
Linear:       1×, 2×, 3×, ...
Exponential:  1×, coefficient×, coefficient²×, ...  (cap at maxInterval)
```

### 幂等性保证

Temporal 是 **at-least-once** 语义，所有 Activity 必须幂等：

```python
@activity.defn
async def execute_agent_task(input: PipelineInput) -> dict:
    info = activity.info()
    # 幂等键 = workflowId / activityId（同一 stage 重试视为同一逻辑操作）
    idempotency_key = f"{info.workflow_id}/{info.activity_id}"

    if cached := await idempotency_store.get(idempotency_key):
        return cached

    result = await call_mcp(input)
    await idempotency_store.put(idempotency_key, result, ttl="24h")
    return result
```

### 幂等键存储

| 后端 | 适用 | 配置 |
|---|---|---|
| Redis（推荐） | 单机 / 集群 | `redis://localhost:6379/0`，TTL 24h |
| SQLite | 单机起步 | `idempotency.db`，与 Temporal SQLite 不同库 |

key 结构：`<namespace>:<workflowId>:<activityId>`，value：`{result_json, created_at}`。

### 副作用幂等模式

对 git push / 部署等操作，Agent 端必须使用：
- 自然幂等：`kubectl apply`（声明式）
- 操作 ID：`POST /deploy { id: <idempotency_key> }`，服务端去重
- 检查后写入：先 query 状态，已生效则跳过

引擎将 `idempotencyKey` 透传给 Agent，由 Agent 配合实现。

### 错误分类总表

| 错误类 | retryable | 处置策略 | 触发场景 |
|---|---|---|---|
| `AuthError` | ❌ | 立即失败 → 告警 | API Key 失效、权限不足 |
| `ToolNotAllowed` | ❌ | 立即失败 → 标 schema bug | Agent 调用未声明工具 |
| `InvalidInput` | ❌ | 立即失败 → 拒绝重提 | 输入 schema 校验失败 |
| `SchemaViolation` | ❌ | 立即失败 | 输出 schema 不符 / 状态写隔离违规 |
| `ApprovalRejected` | ❌ | 走 onFailure | 审批拒绝 |
| `BudgetExceeded` | ❌ | 暂停 + 告警 | LLM 配额耗尽 |
| `TransientError` | ✅ | 退避重试 | 网络抖动、临时 5xx |
| `RateLimited` | ✅ | 退避重试（更长间隔） | LLM 429 |
| `Timeout` | ✅ | 重试 + 调长 timeout | 网络慢、Agent 卡顿 |
| `MCPDisconnect` | ✅ | 等 Agent 恢复后重试 | MCP 长连接断开 |

`stage_failure_total{reason="..."}` 标签化导出，便于按错误类型告警。

---

## 安全设计

### Agent 权限隔离

按 role 限定工具白名单。引擎在 Activity 入口校验：

```python
def check_tool_permission(agent_name: str, tool: str):
    allowed = agent_specs[agent_name].tools
    if tool not in allowed:
        raise ToolNotAllowed(f"{agent_name} cannot use {tool}")
```

**运行时工具调用拦截**：通过 MCP 协议拦截 Agent 的所有 tool call，违规调用立即终止 Activity（而非依赖 Agent 自觉）。

### 密钥管理 + 加密

**Temporal Custom PayloadCodec**：

```python
class EncryptingCodec(PayloadCodec):
    async def encode(self, payloads: list[Payload]) -> list[Payload]:
        return [self._encrypt(p) if self._is_sensitive(p) else p for p in payloads]

    async def decode(self, payloads: list[Payload]) -> list[Payload]:
        return [self._decrypt(p) if self._is_encrypted(p) else p for p in payloads]
```

CLI 配置 KMS 后才能查看明文 input/output；Web UI 显示密文。

### 审计设计

每次关键操作记录到独立审计日志（不仅是 Event History，因 History 受 retention 约束可能丢失）：

```json
{
  "auditId": "audit-xxx",
  "timestamp": "2026-05-16T00:00:00Z",
  "actor": "taoer",
  "action": "pipeline.submit",
  "resource": "abyss-chess/game-dev-pipeline",
  "version": "1.0.0",
  "result": "wf-abc123",
  "ipAddress": "10.0.0.1",
  "userAgent": "orchestra-cli/0.1.0",
  "diff": null
}
```

**审计动作清单**：
`pipeline.{submit, cancel, re-run}` / `approval.{approve, reject}` / `signal.<name>` /
`schedule.{create, pause, resume, delete, trigger}` / `config.update`

**审计存储**：
- 默认 SQLite 表 `audits`（与幂等键库分离）
- 规模化时迁移到 PostgreSQL 同库或独立 DB
- 保留 ≥1 年（合规要求）

Schema：
```sql
CREATE TABLE audits (
    audit_id TEXT PRIMARY KEY,
    ts TIMESTAMP NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    version TEXT,
    result TEXT,
    ip_address TEXT,
    user_agent TEXT,
    diff_json TEXT,
    INDEX idx_actor_ts (actor, ts DESC),
    INDEX idx_resource_ts (resource, ts DESC)
);
```

---

## CLI 设计

```bash
# === 验证与预览 ===
orchestra validate pipeline.yaml
orchestra dry-run  pipeline.yaml
orchestra dry-run  pipeline.yaml --output dot       # graphviz
orchestra dry-run  pipeline.yaml --output mermaid   # mermaid（粘贴到 markdown）

# === 提交 ===
orchestra submit pipeline.yaml
orchestra submit pipeline.yaml --priority high
orchestra submit pipeline.yaml --param target_env=prod
orchestra submit pipeline.yaml --idempotency-key my-unique-id

# === 状态查询 ===
orchestra status                                    # 列表（默认仅 running）
orchestra status <id>                               # 详情
orchestra status <id> --watch
orchestra status <id> -o json|yaml|table
orchestra status <id> --pending-approvals
orchestra status <id> --query "stage:code"          # 调用 workflow.query

# === 运行时控制 ===
orchestra cancel  <id>
orchestra cancel  <id> --force                      # TerminateWorkflow（不走 cleanup）
orchestra approve <id> <stage> [--as <user>]
orchestra reject  <id> <stage> --reason "..."
orchestra re-run  <id> --from <stage>               # 提示 sideEffects 风险
orchestra re-run  <id> --reset-to <stage>           # 从 stage 重置后续 state
orchestra signal  <id> <name> --data '{}'

# === Agent ===
orchestra agents
orchestra agents --label capability=godot
orchestra agents drain <name>                       # 优雅下线（停接新任务）
orchestra agents resume <name>

# === Pipeline 历史 ===
orchestra list                                      # 默认 24h 内
orchestra list --label project=abyss-chess
orchestra list --status failed
orchestra list --since 24h --limit 50

# === 日志 ===
orchestra logs <id>
orchestra logs <id> --stage code
orchestra logs <id> --follow

# === Schedule ===
orchestra schedule create pipeline.yaml --cron "0 9 * * 1-5"
orchestra schedule list
orchestra schedule pause   <schedule-id>
orchestra schedule resume  <schedule-id>
orchestra schedule trigger <schedule-id>            # 立即手动触发一次
orchestra schedule delete  <schedule-id>

# === 调试 ===
orchestra replay <id>                               # Replay 检测代码兼容性
orchestra inspect <id>                              # 导出 Event History
orchestra inspect <id> --download-history out.json  # 用于 Replay 测试 fixture
orchestra diff <id1> <id2>                          # 对比两次运行（state/timing）

# === 健康 ===
orchestra health
orchestra health --agent walnut
orchestra version                                   # CLI / 引擎 / Temporal 版本
```

---

## 测试策略

### 测试金字塔

```
       ┌──────────┐
       │ Chaos    │  故障注入（kill agent / network partition）
       ├──────────┤
       │ E2E      │  完整流水线（mock Agent）
       ├──────────┤
       │ Replay   │  Temporal 兼容性回放（CI 强制）
       ├──────────┤
       │ 集成     │  Schema + DAG + Agent 通信
       ├──────────┤
       │ 单元     │  表达式 / 拓扑 / 超时解析
       └──────────┘
```

详细分层 / 标记 / 验收标准见 `tests/README.md`。

### Temporal Replay 测试（关键）

```python
async def test_pipeline_replay():
    with open("fixtures/wf_history.json") as f:
        history = json.load(f)
    replayer = WorkflowReplayer(workflows=[PipelineWorkflow])
    await replayer.replay_workflow(history)
```

**CI 流程**：
1. 合并前从生产 Temporal 拉取最近 100 个 Workflow History（覆盖 succeeded / failed / cancelled 各类型）
2. 用新代码 Replay
3. 任一失败 → 阻止合并，要求添加 `workflow.get_version()` 兼容代码

**History fixture 治理**：`tests/replay/fixtures/` 目录维护多版本代表性 History，commit 信息标注 "covers feature X"。每次大改打补充 fixture。

### Mock 运行

```bash
orchestra dry-run pipeline.yaml --with-mocks
orchestra dry-run pipeline.yaml --mock-from fixtures/mocks/code_output.json
```

### 混沌测试

```bash
orchestra chaos kill-agent walnut --during stage=code
orchestra chaos kill-temporal --during stage=test
orchestra chaos network-partition strawberry --duration 30s
orchestra chaos slow-agent grape --latency 5s
orchestra chaos corrupt-state <id> --field "$.code.patch"
```

### 性能基线

```python
async def test_submission_latency():
    """提交 → 开始 < 1s"""

async def test_heartbeat_latency():
    """心跳 RTT < 100ms"""

async def test_throughput():
    """1000 Stage 并发执行（mock agent），完成时间 < 60s"""
```

**基线退化检测**：每次发布前对比上一版本，关键指标退化 > 20% 阻止发布。

---

## 可观测性设计

### 关键 Metrics

| Metric | 类型 | 标签 | 用途 |
|--------|------|------|------|
| `pipeline_runs_total` | Counter | namespace, name, status | 运行计数 |
| `pipeline_duration_seconds` | Histogram | namespace, name | P50/P95/P99 |
| `stage_duration_seconds` | Histogram | pipeline, stage, agent | 阶段耗时 |
| `stage_failure_total` | Counter | stage, reason | 错误分类 |
| `agent_heartbeat_lag_seconds` | Gauge | agent | 心跳健康 |
| `agent_busy_slots` | Gauge | agent | 并发占用 |
| `task_queue_depth` | Gauge | queue | 积压告警 |
| `approval_pending_total` | Gauge | pipeline | 待审批数 |
| `pipeline_state_size_bytes` | Histogram | pipeline | State 膨胀监控 |
| `llm_tokens_consumed_total` | Counter | agent, model | 成本核算 |
| `temporal_event_history_size` | Gauge | workflow_id | 接近 50K 上限告警 |
| `pipeline_replay_failure_total` | Counter | — | Replay 失败计数 |

完整命名规范见 `src/orchestra/observability/README.md`。

### Tracing

OpenTelemetry 集成：每个 Stage 是一个 Span，父子关系映射 DAG 拓扑，Agent 内 LLM 调用作为子 Span。Temporal Python SDK 已有 OTel 扩展。

### 必备告警规则

详见 `deploy/prometheus/alerts.yaml`。摘要：

```yaml
- alert: StageFailureSpike
  expr: rate(stage_failure_total[5m]) > 0.1
- alert: AgentDown
  expr: agent_heartbeat_lag_seconds > 60
- alert: TaskQueueBacklog
  expr: task_queue_depth > 20
- alert: ApprovalPending
  expr: approval_pending_total > 0 and time() - approval_started_at > 1800
- alert: WorkflowReplayFailure
  expr: increase(pipeline_replay_failure_total[1h]) > 0
- alert: EventHistoryNearLimit
  expr: temporal_event_history_size > 40000
- alert: LLMCostBudgetExceeded
  expr: increase(llm_tokens_consumed_total[1h]) * cost_per_token > 50
```

---

## 与现有系统集成

### 适配路径

```
现有 MCP Agent
       │ 零改动
       ▼
  AgentAdapter (MCP 封装)
       │ register as Activity
       ▼
  Temporal Worker
       │ activity.heartbeat()
       ▼
  Temporal Server
       │ Workflow DAG 调度
       ▼
  PipelineWorkflow
```

### AgentAdapter 接口契约

```python
class AgentAdapter(Protocol):
    async def execute_task(
        self,
        task: TaskInput,
        on_heartbeat: Callable[[ProgressInfo], None] | None = None,
        resume_from: Checkpoint | None = None,
    ) -> TaskOutput: ...

    async def check_health(self) -> HealthStatus: ...

    async def cancel_task(
        self,
        task_id: str,
        grace_period: timedelta,
    ) -> None: ...

    async def get_capabilities(self) -> AgentCapabilities: ...

    async def get_metrics(self) -> AgentMetrics: ...
```

**能力发现协议**：Worker 启动时调用 `get_capabilities()`，与 YAML 中的 `tools` 求交集；YAML 声明但 Agent 未实现的工具直接报错（fail-fast，不要等运行时）。

---

## 未来扩展

- [x] Web Dashboard（Temporal UI 已覆盖基础）
- [x] Prometheus metrics 导出
- [ ] Pipeline 模板市场（kind: PipelineTemplate）
- [ ] 多项目隔离（Temporal Namespace per project）
- [ ] Agent 自动扩缩（按 task_queue_depth + LLM 配额）
- [ ] Slack / 钉钉 / 企微通知集成
- [ ] Graph-based Replay（类 LangGraph Checkpointer，支持分支 / 回溯调试）
- [ ] Agent 动态发现（mDNS / Consul / k8s Service）
- [ ] Webhook 触发器（GitHub push → 自动跑流水线）
- [ ] Pipeline 灰度发布（新版本先跑 10%）
- [ ] 智能 Agent 调度（基于历史预测 Stage 时长，bin-packing 优化）
- [ ] 跨 Namespace 流水线编排（多项目协作）
- [ ] **PipelineTemplate / PipelineRun 二级资源**（对齐 Tekton），模板与运行实例进一步分离
- [ ] **GitOps 集成**（pipeline.yaml 存 git，引擎 reconcile 到期望状态，类 ArgoCD）
- [ ] **OpenTelemetry 端到端 Tracing**（含 LLM 调用层级）
- [ ] **成本核算面板**（按 namespace / pipeline / agent 维度统计 LLM token 消费与时长）
- [ ] **Agent 沙箱执行**（容器化隔离，避免 Agent bug 影响 Worker 进程）
- [ ] **流水线版本灰度对比**（同输入跑两版本，diff 输出，用于 prompt 调优）
