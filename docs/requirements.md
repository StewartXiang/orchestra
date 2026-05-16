# 需求文档：AI Agent 流水线编排引擎

## 本文档与其他文档关系
- **本文档**：要做什么、为什么（产品 / 业务视角）
- `design.md`：怎么定义（DSL / Schema / 数据流 / CLI）
- `architecture.md`：怎么落地（组件 / 部署 / 可观测）

---

## 背景

当前 Hermes Pipeline 采用手工编排模式：
- 流水线由桃儿（管理者 agent）手工记忆依赖关系并分派任务
- Agent 存活状态通过轮询 `_STATUS.md` 文件检测
- 失败重试靠人工判断
- 没有声明式配置，流水线不可复制 / 不可版本控制
- 缺少持久化的执行历史，流水线中断后无法精确恢复到失败点
- 缺少跨流水线协作机制，多条流水线并发时容易抢占同一 Agent

## 对标目标

像 **Kubernetes** 管理容器集群一样管理 AI Agent 集群，借鉴 **Temporal** 的持久化 Workflow 模型，吸收 **LangGraph** 的图式控制流和 **CrewAI** 的角色协作语义。

### 业界方案对比与选型说明

| 方案 | 优势 | 不足（对我们场景） | 我们如何取舍 |
|------|------|-----------|-----------|
| **Temporal** | 持久化 Workflow、Replay、Signal、Saga、成熟生态 | 学习曲线、Workflow 代码确定性约束 | **作为执行内核**：Workflow + Activity + Signal |
| **K8s Operator (CRD)** | 声明式、Reconcile Loop、社区生态 | 不擅长长流程编排、状态机偏弱 | **借鉴模型**：YAML Spec/Status 分离、Finalizer、Probe |
| **LangGraph** | 图式状态机、与 LLM 集成天然 | 单进程为主、持久化能力弱、缺少分布式 | **借鉴 DSL**：节点/边/条件分支表达 |
| **CrewAI** | 角色（Role）/任务（Task）/工具（Tool）抽象简洁 | 编排能力弱、无重试/补偿/审计 | **借鉴角色模型**：Agent role + capability 标签 |
| **Airflow / Argo Workflows** | DAG 调度成熟 | 偏批处理、不擅长长时人机交互 | **不直接采用**，仅借鉴 DAG schema |
| **AWS Step Functions** | 声明式 ASL、原生云集成 | 厂商锁定、不适合自托管 | **不采用** |

**最终选型**：Temporal（执行内核） + 自研 YAML DSL（参考 K8s + LangGraph） + MCP（Agent 通信） + 自研 Operator（Reconcile Agent 健康）。

### Kubernetes 给我们的启发

| K8s 能力 | 容器场景 | Agent 场景类比 |
|----------|---------|---------------|
| Pod 声明式定义 | `pod.yaml` | `agents:` YAML 定义 Agent 规格 |
| Deployment 滚动更新 | 副本管理 | 流水线版本演进 |
| Liveness Probe | 容器存活检测 | Agent 心跳监控 |
| Readiness Probe | 服务就绪检测 | Agent 健康检查 |
| Startup Probe | 启动检测 | Worker 启动期自检 |
| Service Mesh | 服务发现 / 负载均衡 | Agent 发现 / 任务路由 |
| ConfigMap | 配置注入 | 流水线参数化（非机密配置） |
| Secret | 密钥管理 | API Key / Token 独立管理 |
| Job / CronJob | 任务调度 | Workflow / Schedule 调度 |
| Events + Audit | 事件日志 | Temporal Event History + 独立审计表 |
| ResourceQuota | 资源限制 | Agent 并发上限 / 任务队列深度 |
| Finalizer | 删除保护 | 流水线清理钩子 |
| Status sub-resource | 状态分离 | Pipeline spec/status 分离 |
| PodDisruptionBudget | 干扰预算 | Agent 最小可用数 |
| NetworkPolicy | 网络策略 | Agent 间通信白名单 |
| PriorityClass | Pod 优先级 | 流水线优先级调度 |
| Taints & Tolerations | 节点亲和 | Agent 能力亲和（GPU agent 专用任务） |
| Operator / CRD | 自定义资源管控 | Pipeline / PipelineRun / AgentProfileSet 一等公民资源 |
| HPA | 水平自动扩缩容 | Worker 副本根据 Task Queue 深度自动伸缩 |
| Init Container | Pod 启动前初始化 | Stage 执行前的预处理 Activity |
| Sidecar | 容器伴随进程 | Agent 旁路日志 / 指标采集器 |

### Temporal / LangGraph 给我们的启发

| 能力 | 来源 | 在本系统的对应 |
|------|------|-------------|
| 持久化 Workflow | Temporal | Pipeline 中断可从最后一个事件恢复 |
| Activity 自动重试 | Temporal | 单步骤可配置 RetryPolicy |
| Signal | Temporal | 人工审批、外部事件触发 |
| Update | Temporal 1.21+ | 带返回值的同步审批校验 |
| Query | Temporal | 实时查询流水线进度，无需轮询 |
| Child Workflow | Temporal | 子流水线 / 动态生成的 Stage |
| Continue-As-New | Temporal | 长流水线（>10k 事件）历史压缩 |
| Saga 补偿 | Temporal | 失败时反向调用补偿 Activity |
| Replay 确定性 | Temporal | 升级 Workflow 代码不破坏运行中的实例 |
| 图节点 / 边 DSL | LangGraph | YAML 中 `nodes` + `edges` + `condition` |
| 状态合并（Reducer） | LangGraph | 并行分支结果合并策略（all/any/first/merge/vote/quorum） |
| Role / Task / Tool | CrewAI | Agent 注册时声明 role + capabilities + tools |

## 核心需求

1. **声明式配置**：YAML 定义流水线拓扑、Agent 角色、超时、重试策略
2. **心跳机制**：Agent 定期上报存活状态，超时自动标记死亡
3. **健康检查**：主动 probe Agent 的 MCP endpoint，故障自动转移
4. **DAG 数据流转**：支持依赖解析、并行执行、条件分支、失败补偿
5. **状态持久化**：所有步骤的输入输出持久化，可审计可回溯
6. **人机协作**：审批节点、暂停 / 恢复、运行时注入数据（Signal / Update）
7. **动态编排**：根据中间结果运行时生成新 Stage（Child Workflow）
8. **资源隔离**：同一 Agent 不被多条流水线并发抢占（Mutex）
9. **Pipeline / PipelineRun 分离**：定义可版本化复用，每次执行独立审计

## 现有痛点 → 目标状态

| 痛点 | 现状（手工） | 目标（Agent Orchestra） |
|------|------------|----------------------|
| 流水线定义 | TASK.md 非结构化文本 | YAML 声明式，可版本控制 |
| Agent 存活 | 轮询 `_STATUS.md` | 实时心跳（默认 15s 间隔） |
| 健康检查 | `systemctl status` | 三层探针（startup / readiness / liveness）|
| 任务编排 | 桃儿记忆依赖顺序 | DAG 自动解析依赖 |
| 失败重试 | 桃儿发现→人工重发 | 自动重试 + exponential backoff |
| 状态追踪 | `_PROGRESS.json` 快照 | Temporal Event History 全审计 |
| 并行执行 | 桃儿手动 ko 两个 | DAG 自动并行（`agents: [a, b]`） |
| 条件分支 | 桃儿判断'UI改了没' | `condition: "gdd.has_ui_change"` |
| 故障恢复 | 桃儿改 TASK.md 重来 | Saga 补偿 + 自动回滚 |
| 人工审批 | 桃儿主动询问湘 | `approval` 节点 + Signal/Update |
| 动态任务 | 桃儿看结果再决定下一步 | 运行时动态生成子 Stage（Child Workflow） |
| 审计追溯 | 无（全靠大脑） | 事件历史 + 结构化日志 + 独立审计表 |
| 并发冲突 | 同一 Agent 被并行任务抢占 | Mutex / Semaphore（Task Queue 限流） |
| 流水线优先级 | 紧急任务排队等 | PriorityClass + 抢占式调度 |
| 跨流水线数据共享 | 复制粘贴 / 文件传递 | Workflow 间通过 Signal 或共享存储显式通信 |
| 长任务超时 | 无超时，挂死靠观察 | Activity Heartbeat + 4 级 timeouts |
| 上下文传播 | 无 traceId | OpenTelemetry context 跨 Workflow/Activity 传播 |
| 临时产物管理 | 散落各 Agent 工作区 | 显式 Artifact 概念，统一存储 + 清理 |

## 非功能需求

### 安全需求
- **密钥管理**：API Key / Token 与流水线配置分离，参考 K8s Secret
- **Agent 权限隔离**：按 role 限定工具白名单，不允许越权操作（如 developer 不能 deploy）
- **操作审计**：记录谁在什么时间触发了哪条流水线、每个步骤的输入 / 输出摘要
- **RBAC 基础**：至少区分 admin（提交流水线）和 viewer（查看状态）
- **密钥轮转**：支持 secret 热更新，无需重启 Worker
- **Workflow 输入脱敏**：敏感字段在 Event History 中自动脱敏（Temporal Custom PayloadCodec，AES-256-GCM）
- **mTLS 通信**：Worker ↔ Temporal Server 启用 mTLS（生产环境）
- **供应链安全**：YAML 流水线提交需签名（cosign / minisign 等价方案），防止中间人篡改
- **Agent 能力声明白名单**：Agent 自报的 capability 与服务端注册表（`config/capabilities.yaml`）比对，防止伪造角色
- **Prompt 注入防护**：跨 Stage 数据流转时对 LLM 输入进行边界标记，避免下游 Agent 被上游产物注入指令

### 可观测性需求
- **指标导出**：Prometheus 格式 metrics（流水线成功率、Agent 利用率、DAG 执行时长、LLM token 消耗）
- **结构化日志**：JSON 格式日志，包含 traceId / pipelineId / stageName，敏感字段 redact
- **告警规则**：Agent 死亡 > 1min、流水线失败率 > 阈值、任务队列积压、Replay 失败、LLM 成本超预算
- **分布式追踪**：OpenTelemetry，跨 Workflow → Activity → MCP → LLM 完整链路
- **SLO 定义**：见下文"SLO 与性能基线"
- **Dashboard 模板**：开箱即用的 Grafana Dashboard JSON（流水线 / Agent / 系统三层视图）
- **错误分类**：区分 transient（可重试）vs permanent（需人工介入）错误，分类上报
- **黄金信号**：明确监控四指标——延迟（任务排队 + 执行）、流量（QPS）、错误率（失败 Activity）、饱和度（Worker 任务槽占用）
- **因果链可视化**：Temporal Web UI + 自定义 DAG 视图，支持点击 Stage 跳转 Event History
- **LLM 调用观测**：单独埋点 token 消耗、模型延迟、成本归属到 pipeline / stage / agent 维度

### 运维需求
- **取消机制**：支持取消正在运行的流水线（Temporal Signal cancel）
- **优雅终止**：取消流水线时执行清理 Activity（释放 Agent、清理临时文件）
- **dry-run 模式**：提交前预览 DAG 拓扑和资源匹配，不实际执行
- **重跑能力**：从失败节点重跑流水线（Temporal Reset）
- **流水线版本管理**：Temporal `GetVersion` API 支持 Workflow 代码升级时的兼容
- **数据保留策略**：Event History 保留期可配置（默认 30 天），归档至 S3 长期保存
- **灾难恢复**：Temporal Server 数据库定期备份，RTO < 1h, RPO < 5min
- **容量规划文档**：明确单 Temporal SQLite 实例上限（约 50 并发 Workflow），扩容路径
- **蓝绿 / 金丝雀发布**：Worker 支持双版本并行，新流水线走新 Worker，旧流水线在旧 Worker 跑完
- **配额与限流**：每用户 / 项目级别的并发流水线上限，防止单租户耗尽资源
- **运维 Runbook**：常见故障的标准排查手册（详见 `runbook/`）

### 测试需求
- **Schema 验证**：JSON Schema 静态校验 + DAG 环检测 + Agent 引用完整性
- **Temporal Replay 测试**：确保 Workflow 代码变更后历史事件可重放（CI 强制）
- **流水线模拟运行**：用 mock Agent 跑通 DAG，验证依赖和数据流
- **混沌测试**：随机杀 Agent / 网络分区 / Temporal Server 重启，验证恢复能力
- **性能基线**：见 SLO
- **契约测试**：Agent MCP 接口定义 schema，Worker 与 Agent 双向契约验证
- **流水线快照回归**：核心流水线保留输入 / 输出快照，升级后比对差异
- **负载测试**：模拟 50 条并发流水线，验证 SQLite 不成为瓶颈，给出迁移到 PostgreSQL 的触发阈值

### 可扩展性需求
- **多项目隔离**：Temporal Namespace 按项目（AbyssChess / 新项目）隔离命名空间
- **流水线组合**：支持 Pipeline A 引用 Pipeline B 作为子流水线（Child Workflow）
- **Schedule 调度**：支持 cron 表达式定时触发流水线
- **插件化 Agent**：新增 Agent 只需注册 MCP endpoint，无需改引擎代码
- **多 Worker 副本**：同一 Task Queue 支持多 Worker，自动负载均衡
- **Webhook 触发**：GitHub push / 飞书消息 → 自动触发流水线
- **自定义 Activity SDK**：暴露 Python SDK 让外部开发者贡献新的 Activity 类型
- **事件总线对接**：流水线关键事件（开始 / 失败 / 完成）发布到 NATS / Redis Stream，方便第三方订阅
- **多模态产物**：DAG 数据流支持文本 / JSON / 二进制（图片 / 模型权重 / 视频），通过 Artifact 引用而非内联

## SLO 与性能基线

| 指标 | 目标 | 触发告警阈值 |
|---|---|---|
| Pipeline 成功率（7 日窗口） | ≥ 95% | < 90% |
| Pipeline P95 时长 | < 30 min | > 1 h |
| Pipeline P99 时长 | < 60 min | > 2 h |
| 提交 → 开始执行延迟 | < 1 s | > 5 s |
| 心跳 RTT | < 100 ms | > 500 ms |
| Worker 启动到 ready | < 30 s | > 60 s |
| 单 Workflow Event History | < 10K events | > 40K（需 continue_as_new） |
| 单 Stage State 大小 | < 100 KB（inline）/ 任意（reference） | inline > 1 MB warn |
| LLM 成本 / 流水线 | < $5 平均 | > $50 / h（小时级） |
| 灾难恢复 RTO | < 1 h | — |
| 灾难恢复 RPO | < 5 min | — |

## 当前 Agent 集群规模

- 9 个 profile（核桃 / 杏仁 / 栗子 / 椰子 / 樱桃 / 芒果 / 草莓 / 蓝莓 / 葡萄）
- MCP 通信（端口 18761-18769）
- 角色分类：developer / tester / designer / ci_engineer / chat / standby
- **能力标签（capability tags）**：每个 Agent 声明 `["python", "godot", "ui-design", "playwright"]` 等标签，DAG 调度时按标签匹配，profile 名仅作为运行时实例
- 具体配置见 `config/profiles.yaml`，可用 capability 词表见 `config/capabilities.yaml`

## 项目约束

- **低侵入**：Agent 本身改动最小，通过适配层对接
- **可渐进**：先上 Temporal 替代手工编排，其余模块渐进替换
- **可观测**：CLI `status` / `health` 实时查看集群状态
- **自托管**：Temporal + Worker 全部 docker compose 部署
- **低成本**：Temporal SQLite 模式起步，无需外部数据库
- **单机优先**：默认部署目标为单机（开发者笔记本 / 单台服务器），不强制 K8s
- **离线可用**：Worker 与 Agent 之间在内网即可工作，不依赖外部 SaaS

## 显式非目标（Out of Scope）

- ❌ **不做通用工作流平台**：仅服务 AI Agent 编排，不与 Airflow / Argo 竞争
- ❌ **不做 LLM 推理网关**：模型选择 / 路由由 Agent 自己负责
- ❌ **Phase 1-3 不做多租户计费**：仅做 Namespace 隔离，不涉及成本结算
- ❌ **不做 Web 可视化编辑器**：YAML 优先，可视化最多做只读 DAG 渲染
- ❌ **不替换 MCP 协议**：Agent 通信继续走 MCP，不引入 gRPC / REST 新协议
- ❌ **不做跨地域多活**：单数据中心部署，DR 靠备份恢复

## 关键设计假设与风险

| 假设 | 风险 | 缓解 |
|------|------|------|
| Temporal SQLite 单机够用 | 流水线规模增长后写放大 | 预留 PostgreSQL 迁移路径，负载测试给出阈值（约 50 并发 Workflow） |
| MCP 通信稳定 | 长连接断开导致心跳误判 | 心跳与连接探测分离，2 次失败才标记死亡 |
| Agent 行为可重入 | LLM 非确定性导致重试结果不一致 | Activity 层做幂等键 + 结果缓存 |
| YAML 足够表达 DAG | 复杂条件流变成"YAML 编程" | 明确边界，复杂逻辑下沉到 Activity |
| 单流水线时长 < 24h | 超长流水线触发 Workflow History 上限 | 强制使用 ContinueAsNew 拆分 |
| 9 个 Agent 够用 | 高并发场景单 Agent 成瓶颈 | 同 role 多 profile 副本，capability 路由分担 |

## 验收标准（DoD）

每个里程碑的"完成定义"：

### Phase 1 验收（Temporal 部署 + 基础设施）
- [ ] `docker compose up` 一键启动 Temporal + 所有 Worker
- [ ] 9 个 Agent 全部注册并上报心跳
- [ ] 跑通最小流水线（design-review → code）端到端
- [ ] Replay 测试通过
- [ ] Worker 进程崩溃后自动重启并继续未完成 Workflow
- [ ] Temporal Web UI 可访问，能查看 Event History

### Phase 2 验收（YAML 配置 + DSL）
- [ ] 现有游戏开发流水线 YAML 化，并通过 Schema 验证
- [ ] 与桃儿手工编排 parallel run 3 天，结果一致性 > 95%
- [ ] dry-run 命令可正确预览 DAG
- [ ] 支持 capability 标签匹配 Agent，而非硬编码 profile
- [ ] 条件分支 / 并行 fan-out / fan-in 至少各 1 个真实用例覆盖

### Phase 3 验收（生产切换 + 高级特性）
- [ ] CLI 完整可用（submit / status / cancel / approve / reject / re-run）
- [ ] 失败补偿 Saga 测试通过
- [ ] 人工审批节点测试通过
- [ ] Child Workflow / 动态 Stage 至少 1 个用例上线
- [ ] Mutex 机制验证（同一 Agent 不被并发抢占）
- [ ] 完成 Runbook 文档，新成员可独立处理 P2 故障

### Phase 4 验收（可观测 + 韧性）
- [ ] Prometheus metrics 全量上报
- [ ] Grafana Dashboard 部署
- [ ] 告警规则触发测试通过
- [ ] 审计日志可查询
- [ ] 混沌测试通过：随机杀 Worker / Agent，流水线 100% 最终完成或明确失败
- [ ] SLO 报表自动生成，并接入告警
- [ ] LLM 成本归因报表（按 pipeline / stage / agent 维度）可用

## 术语表（Glossary）

避免概念混淆，统一用词：

- **Pipeline**：一次完整业务流程的**定义**（如"开发新功能"），可版本化复用
- **PipelineRun**：Pipeline 的一次具体**执行**实例，每次提交产生一个新 Run
- **Stage / Node**：流水线中的一个步骤（DAG 节点）
- **Workflow**：Temporal 中持久化的执行实例（一个 PipelineRun 对应一个 Workflow）
- **Activity**：Workflow 调用的原子操作（如"调用 Agent X 完成任务 Y"）
- **Agent**：执行实际工作的 LLM 实例（核桃、杏仁等）
- **Worker**：运行 Workflow / Activity 代码的进程，对接 Temporal Server
- **Profile**：Agent 的物理身份（含 MCP 端口、工具配置）
- **Role**：Agent 的逻辑角色（developer / tester / designer 等）
- **Capability**：Agent 的能力标签（python / godot / ui-design 等）
- **Artifact**：流水线产生的可持久化产物（代码、图片、报告）
- **Signal**：外部向 Workflow 发送的异步事件（取消、注入数据）
- **Update**：Temporal 1.21+ 的同步带返回值交互（如审批校验）
- **Saga**：失败时反向调用补偿 Activity 的事务模式
