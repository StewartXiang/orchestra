# Agent Orchestra 使用文档

> 像 Kubernetes 管理 Pod 一样管理 AI Agent — 声明式 YAML 定义流水线，Temporal 驱动执行，MCP 通信 Agent。

---

## 目录

1. [项目简介](#1-项目简介)
2. [快速开始](#2-快速开始)
3. [部署与启动](#3-部署与启动)
4. [编写 Pipeline YAML](#4-编写-pipeline-yaml)
5. [CLI 命令参考](#5-cli-命令参考)
6. [Agent 管理](#6-agent-管理)
7. [流水线高级特性](#7-流水线高级特性)
8. [可观测性](#8-可观测性)
9. [故障排查](#9-故障排查)
10. [开发与测试](#10-开发与测试)
11. [关键概念词汇表](#11-关键概念词汇表)

---

## 1. 项目简介

**Agent Orchestra** 是一个 AI Agent 流水线编排引擎，解决多 LLM Agent 协作时的以下痛点：

| 痛点 | Orchestra 的解决方案 |
|---|---|
| 手工依赖排序、容易出错 | 声明式 YAML DAG，自动解析拓扑 |
| Agent 宕机无感知 | 15s 心跳 + 三层探针，自动故障转移 |
| 失败靠人工重试 | 指数退避自动重试 + Saga 补偿 |
| 没有执行历史 | Temporal Event History 全量审计 |
| 并发抢占同一 Agent | Task Queue 隔离 + maxConcurrency 限流 |
| 无法版本化流程 | Pipeline YAML 纳入 Git 版本控制 |

**技术栈**

```
用户 / CI/CD
    │  orchestra CLI / API
    ▼
Temporal Server（编排引擎）
    │  SDK gRPC
    ▼
Agent Worker 进程（9 个，各对应一个 MCP Agent）
    │  MCP HTTP
    ▼
LLM Agent（核桃 / 杏仁 / 栗子...）
```

---

## 2. 快速开始

### 2.1 环境要求

- Python 3.11+
- Docker + Docker Compose
- uv（Python 包管理器，可选但推荐）

### 2.2 安装 CLI

```bash
# 方式一：uv（推荐）
uv tool install -e .

# 方式二：pip
pip install -e .

# 验证安装
orchestra --help
```

### 2.3 五分钟跑通第一条流水线

```bash
# 1. 启动完整服务栈
docker compose -f deploy/docker-compose.yml up -d

# 等待 Temporal 就绪（约 15s）
docker compose -f deploy/docker-compose.yml ps

# 2. 校验示例流水线
orchestra validate examples/minimal.pipeline.yaml

# 3. 预览 DAG 拓扑（不实际执行）
orchestra dry-run examples/minimal.pipeline.yaml

# 4. 提交执行
orchestra submit examples/minimal.pipeline.yaml \
    --param task="实现一个 hello world 函数"

# 5. 查看状态
orchestra status
orchestra status <workflow-id> --watch

# 6. 查看集群健康
orchestra health
```

---

## 3. 部署与启动

### 3.1 服务组成

`deploy/docker-compose.yml` 包含以下服务：

| 服务 | 地址 | 说明 |
|---|---|---|
| `temporal-server` | `localhost:7233` | Temporal 编排引擎（SQLite 持久化） |
| `temporal-ui` | `http://localhost:8080` | Temporal Web 控制台 |
| `prometheus` | `http://localhost:9090` | 指标采集 |
| `grafana` | `http://localhost:3000` | 监控看板（admin/admin） |
| `otel-collector` | `localhost:4317` | OpenTelemetry 网关 |
| `redis` | `localhost:6379` | 幂等键存储（生产推荐） |
| `worker-walnut` ~ `worker-grape` | 各 9100 | 9 个 Agent Worker |

### 3.2 启动全套服务

```bash
# 启动所有服务
docker compose -f deploy/docker-compose.yml up -d

# 仅启动 Temporal（不启动 Worker，适合本地开发）
docker compose -f deploy/docker-compose.yml up -d temporal-server temporal-ui

# 查看日志
docker compose -f deploy/docker-compose.yml logs -f worker-walnut

# 停止（保留数据）
docker compose -f deploy/docker-compose.yml stop

# 停止并清除数据（谨慎！）
docker compose -f deploy/docker-compose.yml down -v
```

### 3.3 环境变量配置

Worker 进程通过环境变量配置，可在 `docker-compose.yml` 中覆盖：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROFILE_NAME` | 必填 | Agent profile 名（如 `walnut`） |
| `TEMPORAL_HOST` | `localhost:7233` | Temporal Server 地址 |
| `TEMPORAL_NAMESPACE` | `default` | Temporal Namespace |
| `MCP_ENDPOINT` | 来自 profiles.yaml | 覆盖 MCP 端口 |
| `METRICS_PORT` | `9100` | Prometheus 指标端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `REDIS_URL` | — | Redis URL（不设则用 SQLite） |
| `ORCHESTRA_ENCRYPTION_KEY` | — | 32 字节 hex，Payload 加密密钥 |

### 3.4 CLI 全局选项

所有 `orchestra` 命令支持以下全局选项（优先级高于环境变量）：

```bash
orchestra --host localhost:7233 \
          --namespace default \
          --output table|json|yaml \
          <子命令>
```

也可以通过环境变量设置：
```bash
export TEMPORAL_HOST=temporal-server:7233
export TEMPORAL_NAMESPACE=abyss-chess
```

---

## 4. 编写 Pipeline YAML

### 4.1 最简示例

```yaml
apiVersion: orchestra.io/v1
kind: Pipeline
metadata:
  name: hello-pipeline
  namespace: default
spec:
  agents:
    walnut:
      role: developer
      mcpEndpoint: "mcp://localhost:18761"
      tools: [file_read, file_write]

  pipeline:
    stages:
      - name: code
        agent: walnut
        input: "$.params.task"
        output: "$.code.result"
        timeouts:
          startToClose: 10m
          heartbeat: 30s

  parameters:
    - name: task
      type: string
      required: true

  global:
    heartbeatInterval: 15s
    timeouts:
      workflowExecution: 1h
```

### 4.2 顶层结构

```yaml
apiVersion: orchestra.io/v1    # 固定值
kind: Pipeline                  # 资源类型
metadata:
  name: my-pipeline             # 必填，DNS-1123 格式（小写字母/数字/连字符）
  namespace: default            # 必填，对应 Temporal Namespace
  version: 1.0.0                # 语义化版本（可选）
  labels:
    project: abyss-chess
  annotations:
    owner: "team@example.com"
spec:
  agents: { ... }               # Agent 声明
  pipeline: { ... }             # 流水线 DAG 定义
  parameters: [ ... ]           # 运行时参数定义
  secrets: [ ... ]              # 密钥引用
  global: { ... }               # 全局策略
```

### 4.3 Agent 声明

```yaml
spec:
  agents:
    walnut:                             # Agent 名称（在本 Pipeline 中唯一）
      role: developer                   # 角色（developer/tester/designer/ci_engineer/chat/standby）
      capabilities: [python, godot]     # 能力标签（来自 config/capabilities.yaml）
      mcpEndpoint: "mcp://localhost:18761"
      tools: [file_read, file_write, git_commit]   # 工具白名单
      maxConcurrency: 2                 # 最大并发任务数
      resources:
        limits:
          tokensPerMinute: 100000       # LLM Token 速率限制
      livenessProbe:
        heartbeatInterval: 15s          # 心跳间隔
        gracePeriod: 45s                # 超过此时间无心跳 → 标记 DEAD
      readinessProbe:
        endpoint: "mcp://localhost:18761/health"
        periodSeconds: 30s
        failureThreshold: 3
      retry:
        maxAttempts: 3
        backoff: exponential            # fixed | linear | exponential
        initialInterval: 10s
        maxInterval: 5m
        coefficient: 2.0
        nonRetryableErrors:             # 这些错误不重试，直接失败
          - AuthError
          - ToolNotAllowed
```

也可以用 **能力选择器**代替直接绑定 Agent 名称：

```yaml
- name: code
  agentSelector:
    role: developer
    capabilities: [godot]              # 自动匹配满足条件的 Agent
```

### 4.4 Stage 定义

#### 基本属性

```yaml
- name: code                           # 唯一名称（DNS-1123）
  agent: walnut                        # 执行此 Stage 的 Agent
  dependsOn: [design-review]           # 前驱 Stage 列表（定义 DAG 边）
  condition: 'gdd.has_ui_change'       # 条件表达式（false → 跳过）
  priority: 50                         # 0-100，影响任务队列出队顺序
  input: "$.gdd.task"                  # 从全局 State 读取输入（JSONPath）
  output: "$.code.patch"               # 写入全局 State 的路径（JSONPath）
  onFailure: fail                      # fail | continue | compensate
```

#### 超时配置

```yaml
  timeouts:
    scheduleToStart: 1m                # 任务在队列等待的最长时间
    startToClose: 30m                  # 单次执行最长时间（最重要）
    scheduleToClose: 2h                # 含重试的总超时（设置后关闭重试计数）
    heartbeat: 30s                     # 心跳超时（必须 < startToClose）
```

#### 并行执行与聚合

```yaml
- name: ui-verify
  agents: [strawberry, grape]          # 并行执行多个 Agent
  aggregateStrategy: all               # all | any | first | merge | vote | quorum
  quorumThreshold: 0.66                # 仅 quorum 策略时有效
```

| 聚合策略 | 含义 |
|---|---|
| `all`（默认）| 所有 Agent 成功才算成功 |
| `any` | 任意一个成功即可 |
| `first` | 第一个完成的为准（其余取消） |
| `merge` | 合并所有输出为 dict |
| `vote` | 多数投票（n/2+1） |
| `quorum` | 自定义阈值（`quorumThreshold`） |

### 4.5 条件表达式

`condition` 字段支持 CEL 表达式：

```yaml
# 简单相等
condition: 'test.result == "pass"'

# 逻辑组合
condition: 'test.result == "pass" and size(diagnosis.bugs) < 10'

# 数量判断
condition: 'size(diagnosis.bugs) > 0'

# 布尔字段
condition: 'gdd.has_ui_change'
```

**注意**：`condition` 为 `false` 时该 Stage 被标记为 `SKIPPED`（不阻塞后续），抛出异常时才标记为 `FAILED`。

### 4.6 人工审批节点

```yaml
- name: deploy-approval
  dependsOn: [ci-gate]
  approval:
    approvers: [ou_alice, ou_bob]      # 审批人 ID
    policy: any                        # any（任一）| all（全部）| quorum
    message: "CI 通过，是否部署到生产？"
    timeout: 1h                        # 审批等待超时
    onTimeout: reject                  # reject | approve | escalate
    escalateTo: ou_manager             # 超时后升级审批人
    reminderInterval: 15m              # 重复提醒间隔
    contextFields:                     # 通知卡片展示的 State 字段
      - "$.ci.report"
      - "$.test.coverage"
```

审批操作：

```bash
orchestra approve <pipeline-id> deploy-approval --as ou_alice
orchestra reject  <pipeline-id> deploy-approval --reason "需要修复安全漏洞"
```

### 4.7 动态 for_each

运行时根据数据动态展开 Stage：

```yaml
- name: fix-each
  dependsOn: [diagnose]
  dynamic:
    generator: for_each
    input: "$.diagnose.bugs"           # 从 State 读取列表
    maxParallel: 3                     # 最大并行数
    maxItems: 50                       # 处理上限
    onItemFailure: continue            # continue | fail_fast
    aggregateOutput: "$.fixes"         # 汇总结果写入路径
    template:
      name: "fix-bug-{{ item.id }}"
      agent: walnut
      input: "$.item"
      output: "$.fix"
      timeouts: {startToClose: 10m, heartbeat: 30s}
```

### 4.8 Saga 补偿

当 Stage 失败时自动执行回滚：

```yaml
spec:
  pipeline:
    stages:
      - name: deploy
        agent: coconut
        onFailure: compensate          # 失败时触发补偿
        ...
    compensation:
      strategy: reverse                # reverse（逆序）| parallel | custom
      maxCompensationAttempts: 2
      onCompensationFailure: alert
      actions:
        - forStage: deploy
          agent: coconut
          action: rollback
          runOn: any_failure
```

### 4.9 产出物（Artifacts）

```yaml
- name: build
  agent: coconut
  artifacts:
    - name: game_build
      path: /opt/godot/build/
      type: directory
      retention: 7d
      compress: true
      storageClass: local              # local | s3 | oss
      hash: sha256

# 下游引用产出物
- name: test
  inputArtifacts:
    - from: build/game_build           # <stage>/<artifact-name>
      as: build_output
```

### 4.10 Stage 缓存

相同输入可复用结果，节省 LLM 调用成本：

```yaml
- name: analyze
  cache:
    key: "{{ inputs.gdd.task | sha256 }}"
    ttl: 24h
    enabled: true
```

### 4.11 大数据处理（output.storage）

Temporal State 有 2MB 限制，大输出需用外部引用：

```yaml
- name: code
  output:
    path: "$.code.patch"
    storage: reference                 # inline | reference | oss
    # storage: oss
    # bucket: "orchestra-artifacts"
    # ttl: 30d
```

### 4.12 运行时参数

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
# 命令行参数
orchestra submit pipeline.yaml --param target_env=prod --param skip_tests=false

# 或 values 文件
orchestra submit pipeline.yaml --values examples/values/prod.values.yaml
```

模板内引用：`{{ params.target_env }}`

---

## 5. CLI 命令参考

### 5.1 校验与预览

```bash
# 静态校验（JSON Schema + DAG 环检测 + 引用完整性）
orchestra validate pipeline.yaml

# 预览 DAG（不执行）
orchestra dry-run pipeline.yaml
orchestra dry-run pipeline.yaml --output mermaid   # Mermaid 图
orchestra dry-run pipeline.yaml --output dot       # Graphviz

# 提交前预演（含参数渲染，不实际提交）
orchestra submit pipeline.yaml --dry-run --param task="hello"
```

### 5.2 提交与运行

```bash
# 基本提交
orchestra submit pipeline.yaml

# 带参数
orchestra submit pipeline.yaml -p task="实现功能X" -p env=prod

# 带 values 文件
orchestra submit pipeline.yaml --values prod.values.yaml

# 指定优先级
orchestra submit pipeline.yaml --priority high

# 防重提交（幂等键）
orchestra submit pipeline.yaml --idempotency-key release-2026-05-16
```

### 5.3 状态查询

```bash
# 列出最近 20 条运行中的流水线
orchestra status

# 查看指定流水线详情
orchestra status <workflow-id>

# 持续监控（3s 刷新）
orchestra status <workflow-id> --watch

# 查看待审批节点
orchestra status <workflow-id> --pending-approvals

# 执行 Workflow Query（实时读取内部状态）
orchestra status <workflow-id> --query get_progress
```

### 5.4 运行时控制

```bash
# 取消流水线（走清理逻辑）
orchestra cancel <workflow-id>

# 强制终止（不走清理）
orchestra cancel <workflow-id> --force

# 审批通过
orchestra approve <workflow-id> <stage-name> --as ou_alice

# 拒绝审批
orchestra reject <workflow-id> <stage-name> --reason "风险太高"

# 重跑（从指定 Stage 开始）
orchestra re-run <workflow-id> --from <stage-name>

# 发送自定义 Signal
orchestra signal <workflow-id> pause --data '{}'
orchestra signal <workflow-id> resume --data '{}'
```

### 5.5 历史与日志

```bash
# 列出流水线（默认 24h 内）
orchestra list
orchestra list --status failed
orchestra list --since 7d --limit 100

# 查看日志
orchestra logs <workflow-id>
orchestra logs <workflow-id> --stage code
orchestra logs <workflow-id> --follow

# 导出完整 Event History（供 Replay 测试用）
orchestra inspect <workflow-id>
orchestra inspect <workflow-id> --download-history out.json
```

### 5.6 定时调度

```bash
# 创建定时触发（cron 表达式，本地时区）
orchestra schedule create pipeline.yaml --cron "0 9 * * 1-5"

# 列出所有 Schedule
orchestra schedule list

# 暂停 / 恢复
orchestra schedule pause   <schedule-id>
orchestra schedule resume  <schedule-id>

# 立即手动触发一次
orchestra schedule trigger <schedule-id>

# 删除
orchestra schedule delete  <schedule-id>
```

### 5.7 调试工具

```bash
# Replay 测试（检测代码升级后的兼容性）
orchestra replay <workflow-id> --history-file fixtures/linear_happy.json

# 查看 Event History（调试用）
orchestra inspect <workflow-id>

# 健康检查
orchestra health                         # 检查所有 Agent
orchestra health --agent walnut          # 检查指定 Agent
orchestra health --no-mcp               # 跳过 MCP 探针，仅检查配置
```

### 5.8 故障注入（测试环境）

```bash
# 杀死 Worker（测试 failover）
orchestra chaos kill-agent walnut --during stage=code

# 重启 Temporal Server（测试持久化恢复）
orchestra chaos kill-temporal

# 模拟网络分区
orchestra chaos network-partition strawberry --duration 30s
```

---

## 6. Agent 管理

### 6.1 当前集群（9 个 Agent Profile）

| Profile | 角色 | 能力标签 | MCP 端口 |
|---|---|---|---|
| `walnut`（核桃） | developer | python, godot, gdscript, git | 18761 |
| `almond`（杏仁） | tester | python, pytest, coverage, playwright | 18762 |
| `chestnut`（栗子） | developer | python, web, fastapi, async | 18763 |
| `coconut`（椰子） | ci_engineer | docker, deploy, k8s, terraform | 18764 |
| `cherry`（樱桃） | designer | ui-design, figma, asset-export | 18765 |
| `mango`（芒果） | developer | godot, gdscript, shader, gameplay | 18766 |
| `strawberry`（草莓） | tester | playwright, ui-test, e2e | 18767 |
| `blueberry`（蓝莓） | chat | chat, summarize, translate | 18768 |
| `grape`（葡萄） | standby | generic, fallback | 18769 |

### 6.2 查看 Agent 状态

```bash
# 列出所有 Agent
orchestra agents list

# 按能力标签过滤
orchestra agents list --label capability=godot

# 查看集群健康（含 MCP 探针）
orchestra health
```

### 6.3 优雅下线与恢复

```bash
# 下线 Agent（停止接受新任务，已有任务继续跑完）
orchestra agents drain walnut

# 恢复接受任务
orchestra agents resume walnut
```

### 6.4 在 Pipeline 中绑定 Agent

**直接绑定**（精确指定）：
```yaml
- name: code
  agent: walnut
```

**能力路由**（自动匹配）：
```yaml
- name: code
  agentSelector:
    role: developer
    capabilities: [godot]
```

**并行多 Agent**：
```yaml
- name: ui-verify
  agents: [strawberry, grape]
  aggregateStrategy: vote
```

### 6.5 新增 Agent Profile

编辑 `config/profiles.yaml`，按格式添加新 profile：

```yaml
profiles:
  my-agent:
    role: developer
    capabilities: [python, my-framework]
    mcpEndpoint: "mcp://localhost:18770"
    model: deepseek-v4-pro
    tools: [file_read, file_write, shell_run]
    maxConcurrency: 1
    description: "我的新 Agent"
```

能力标签必须先在 `config/capabilities.yaml` 中声明，然后在 `deploy/docker-compose.yml` 中添加对应 Worker 服务。

---

## 7. 流水线高级特性

### 7.1 多阶段 DAG 模式

```
# 线性链
design → code → test → deploy

# 扇出（并行）
design → {code, art}

# 扇入（汇聚）
{code, art} → test

# 条件分支
test → [pass→deploy, fail→fix→test]

# 混合
design → {code, art} → test → approve → deploy
```

完整游戏开发流水线示例见 `examples/game-dev.pipeline.yaml`。

### 7.2 流水线参数化（多环境）

一份 Pipeline YAML，多套 values 文件：

```bash
# 开发环境
orchestra submit pipeline.yaml --values examples/values/staging.values.yaml

# 生产环境
orchestra submit pipeline.yaml --values examples/values/prod.values.yaml

# 临时覆盖
orchestra submit pipeline.yaml --values prod.values.yaml -p skip_tests=true
```

### 7.3 副作用声明与安全重跑

```yaml
- name: deploy
  sideEffects: [deploy, network]      # 声明有外部副作用
```

`re-run --from deploy` 时 CLI 会显示副作用警告，提示手动确认。

### 7.4 子流水线

```yaml
- name: ui-test
  childWorkflow:
    name: ui-test-pipeline
    version: 1.0.0
    parentClosePolicy: TERMINATE       # TERMINATE | ABANDON | REQUEST_CANCEL
```

### 7.5 长流水线分段（continue_as_new）

当流水线步骤超过 100 个，引擎自动截断 Event History，避免达到 Temporal 50K 事件上限，无需手动处理。

---

## 8. 可观测性

### 8.1 Grafana 看板

启动后访问 `http://localhost:3000`（默认账号 admin/admin）：

| 看板 | 内容 |
|---|---|
| **Pipeline** | 运行计数、P95 时长、Stage 失败分布、待审批数 |
| **Agent** | Agent 状态、心跳延迟、并发占用、LLM Token 消耗 |
| **System** | Worker 存活数、Task Queue 深度与延迟、Event History 大小 |

### 8.2 关键指标说明

| 指标 | 告警阈值 | 含义 |
|---|---|---|
| `agent_heartbeat_lag_seconds` | > 60s | Agent 心跳超时 |
| `task_queue_depth` | > 20 | 队列积压 |
| `stage_failure_total{reason}` | 速率 > 0.1/5min | Stage 频繁失败 |
| `approval_pending_total` | > 0 且 30min+ | 审批长时间未处理 |
| `temporal_event_history_size` | > 40000 | 即将触发 continue_as_new |
| `llm_tokens_consumed_total` | 小时成本 > $50 | LLM 费用超预算 |

### 8.3 Temporal Web UI

访问 `http://localhost:8080`，可查看：
- 所有 Workflow 的执行历史与状态
- 每个 Stage（Activity）的输入/输出
- 完整 Event History 时间线
- Signal/Update/Query 操作记录

### 8.4 结构化日志

```bash
# 查看 Worker 日志（JSON 格式）
docker compose -f deploy/docker-compose.yml logs worker-walnut | jq .

# 按 pipeline 过滤
docker compose -f deploy/docker-compose.yml logs worker-walnut | \
    jq 'select(.pipelineId == "my-pipeline-001")'
```

### 8.5 告警配置

告警规则在 `deploy/prometheus/alerts.yaml`，开箱即用，包含：
- `AgentDown`（critical）
- `PipelineHighFailureRate`（warning）
- `TaskQueueBacklog`（warning）
- `WorkflowReplayFailure`（critical）
- `LLMCostBudgetExceeded`（warning）

---

## 9. 故障排查

### 9.1 流水线一直 Pending

**可能原因**：Worker 未启动或 Task Queue 不匹配。

```bash
# 检查 Worker 状态
orchestra health
docker compose -f deploy/docker-compose.yml ps

# 查看 Temporal UI 确认 Task Queue 是否有 Poller
# http://localhost:8080 → Task Queues
```

### 9.2 Agent 心跳超时

**现象**：`stage_failure_total{reason="TimeoutError"}` 增长，`agent_heartbeat_lag_seconds` > 60。

```bash
# 查看 Worker 日志
docker compose logs worker-walnut --tail 100

# 重启 Worker（已有任务会 failover）
docker compose restart worker-walnut
```

详见 `runbook/01-agent-heartbeat-flap.md`。

### 9.3 Schema 校验失败

```bash
# 查看详细错误
orchestra validate pipeline.yaml

# 常见错误
# - stage.agent 未在 spec.agents 中声明
# - heartbeat > startToClose（超时关系错误）
# - DNS-1123 命名违规（含大写/下划线）
# - 依赖节点不存在
```

### 9.4 审批节点无响应

```bash
# 查看待审批节点
orchestra status <workflow-id> --pending-approvals

# 手动审批
orchestra approve <workflow-id> deploy-approval --as ou_xxx

# 手动拒绝
orchestra reject  <workflow-id> deploy-approval --reason "暂缓部署"
```

详见 `runbook/04-approval-pending.md`。

### 9.5 误提交了错误的流水线

```bash
# 优雅取消（走清理）
orchestra cancel <workflow-id>

# 确认有没有副作用
orchestra status <workflow-id>

# 若已触发 deploy，手动跑补偿
orchestra signal <workflow-id> override --data '{"stage": "deploy", "action": "rollback"}'
```

详见 `runbook/07-misfired-pipeline.md`。

### 9.6 连接 Temporal 失败

```bash
# 检查 Temporal 是否启动
docker compose ps temporal-server

# 检查端口
curl -s http://localhost:7233 || echo "Temporal 未启动"

# 启动 Temporal
docker compose -f deploy/docker-compose.yml up -d temporal-server
```

---

## 10. 开发与测试

### 10.1 本地开发环境

```bash
# 安装依赖
uv sync --all-extras

# 仅启动 Temporal（不需要完整 docker 栈）
docker compose -f deploy/docker-compose.yml up -d temporal-server temporal-ui
```

### 10.2 运行测试

```bash
# 单元测试（无 IO 依赖，快速）
pytest tests/unit/ -v

# 集成测试（需要 temporalio 包，in-process 测试环境）
pytest tests/integration/ -v

# Replay 兼容性测试（保护 Workflow 代码不引入非确定性）
pytest tests/replay/ -v

# 混沌测试（纯逻辑部分，不需要 Docker）
pytest tests/chaos/ -v -m chaos

# 负载测试（发布前跑）
pytest tests/load/ -v -m load

# 全量
pytest tests/
```

### 10.3 录制 Replay Fixture

每次修改 Workflow 代码后，需要录制新的 Replay fixture 并跑兼容性回归：

```bash
# 录制（in-process 环境）
python scripts/record_fixtures.py

# 或从真实 Temporal 录制（需要先跑一次流水线）
temporal workflow show -w <workflow-id> --output json \
    > tests/replay/fixtures/my_scenario.json

# 验证 Replay 通过
pytest tests/replay/ -v
```

### 10.4 代码质量

```bash
# 格式检查
ruff check src tests

# 类型检查
mypy --strict src

# 运行完整 CI 流程
ruff check src tests && mypy --strict src && pytest tests/unit/ tests/replay/
```

### 10.5 修改 Workflow 代码的注意事项

Workflow 代码有**确定性约束**，违反会导致 Replay 失败：

```python
# ❌ 禁止
time.now() / datetime.now()   → 用 workflow.now()
time.sleep()                   → 用 await workflow.sleep()
random.random()                → 用 workflow.random()
import requests / aiohttp      → 包成 Activity
raise Exception("msg")         → 用 raise ApplicationError("msg", non_retryable=True)
```

修改 Workflow 后若破坏已有 History，需添加版本兼容代码：

```python
from temporalio import workflow

version = workflow.get_version("change-name", workflow.DEFAULT_VERSION, 1)
if version == workflow.DEFAULT_VERSION:
    # 旧逻辑
else:
    # 新逻辑
```

---

## 11. 关键概念词汇表

| 术语 | 含义 |
|---|---|
| **Pipeline** | 业务流程的**定义**（YAML），可版本化复用 |
| **PipelineRun** | Pipeline 的一次**执行**实例，每次提交产生一个 |
| **Stage** | 流水线中的一个执行节点（DAG 节点） |
| **Workflow** | Temporal 内部的持久化执行实例（对应一个 PipelineRun） |
| **Activity** | Workflow 调用的原子操作（如"调用 walnut 完成 code stage"） |
| **Agent** | 实际执行任务的 LLM 进程（核桃、杏仁等） |
| **Worker** | 运行 Workflow/Activity 代码的 Python 进程，对接 Temporal |
| **Profile** | Agent 的物理身份（含 MCP 端口、工具配置） |
| **Role** | Agent 的逻辑角色（developer / tester / designer 等） |
| **Capability** | Agent 的能力标签（python / godot / playwright 等） |
| **Task Queue** | Temporal 任务队列，每个 Agent 独占一个 |
| **Artifact** | 流水线产出物（代码、构建包、图片等） |
| **Signal** | 异步发给 Workflow 的外部事件（取消、暂停等） |
| **Update** | 同步带返回值的 Workflow 交互（审批） |
| **Query** | 只读查询 Workflow 内部状态（进度、DAG 状态） |
| **Saga** | 失败时反向调用补偿 Activity 的事务模式 |
| **Event History** | Temporal 记录的完整 Workflow 执行日志（可审计、可 Replay） |
| **Replay** | 用历史 Event History 回放 Workflow 代码，验证确定性 |
| **continue_as_new** | 截断超长 Workflow History，自动结转状态到新实例 |
| **幂等键** | 防止同一操作被重复执行的唯一键（`workflow_id/activity_id`） |

---

## 附录

### A. 目录结构速查

```
/opt/orchestra/
├── docs/              # 需求 / 设计 / 架构文档
├── examples/          # 示例 Pipeline YAML
├── config/            # Agent profiles + capabilities 词表
├── schema/            # JSON Schema（Pipeline / PipelineRun）
├── deploy/            # Docker Compose + Prometheus + Grafana
├── src/orchestra/     # 源码
│   ├── domain/        # 纯类型定义（零依赖）
│   ├── schema/        # YAML 解析 + DAG 校验
│   ├── workflows/     # Temporal Workflow（确定性域）
│   ├── activities/    # Temporal Activity（副作用域）
│   ├── adapters/      # MCP Agent 通信层
│   ├── state/         # State 管理 + 产出物存储
│   ├── observability/ # Metrics / Tracing / 审计
│   ├── worker/        # Worker 进程入口
│   └── cli/           # orchestra CLI 命令
├── tests/             # 单元 / 集成 / Replay / Chaos / 负载测试
├── runbook/           # 故障处置 SOP（7 篇）
└── scripts/           # 辅助脚本（录制 fixture 等）
```

### B. 常用端口

| 端口 | 服务 |
|---|---|
| 7233 | Temporal Server（gRPC） |
| 8080 | Temporal Web UI |
| 9090 | Prometheus |
| 3000 | Grafana |
| 4317 | OTel Collector（gRPC） |
| 4318 | OTel Collector（HTTP） |
| 6379 | Redis |
| 18761~18769 | Agent MCP 端口（walnut~grape） |
| 9100~9108 | Worker Metrics（各 Worker） |

### C. SLO 目标

| 指标 | 目标 |
|---|---|
| Pipeline 成功率（7 日窗口） | ≥ 95% |
| Pipeline P95 时长 | < 30 分钟 |
| 提交 → 开始执行延迟 | < 1 秒 |
| 心跳 RTT | < 100 毫秒 |
| 灾难恢复 RTO | < 1 小时 |
| 灾难恢复 RPO | < 5 分钟 |
