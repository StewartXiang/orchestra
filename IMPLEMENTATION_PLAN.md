# 代码生成实施路线图

> 给代码生成 agent 看的实施清单。**严格按顺序**，下层模块没完成不要写上层。
>
> 每个步骤完成 = 通过该步骤的 DoD（Definition of Done）+ 不破坏 CI。

## 总原则

1. **自底向上**：domain → schema → state/adapters/observability → activities → workflows → worker → cli
2. **契约先行**：每个模块先固化 Protocol / Pydantic 类型，再写实现
3. **小步提交**：一个 PR 一个模块（或一个模块的一部分），不要一次性铺开
4. **测试同 PR**：实现和单元测试在同一 commit，replay fixture 可以延迟一个 PR
5. **不许跳级**：写 workflows 之前 activities 必须可跑通；写 cli 之前 worker 必须能起来

## 阶段总览

| Phase | 目标 | 大致工作量 | 阻塞依赖 |
|---|---|---|---|
| P0 | domain 类型契约 | 1 PR | 无 |
| P1 | schema 解析 + DAG 校验 | 2-3 PR | P0 |
| P2 | state / adapters / observability | 3-4 PR | P0 |
| P3 | activities | 2-3 PR | P0, P2 |
| P4 | workflows（含 signal/query/update） | 3-4 PR | P0, P3 |
| P5 | worker + 端到端跑通 | 1-2 PR | P3, P4 |
| P6 | cli 全套子命令 | 4-5 PR | P5 |
| P7 | 部署联调 + Replay fixture 落库 | 持续 | P5 |

预估：~20 PR 跑完一轮 minimal pipeline 端到端。

---

## P0 — domain（1 PR）

**目标**：所有上层模块共用的纯类型定义就绪，零外部依赖。

**生成顺序**：
1. `enums.py`（Phase / AggregateStrategy / OutputStorage / Priority / BackoffKind / Role / Severity）
2. `errors.py`（OrchestraError 基类 + 11 个错误子类，详见 design.md "错误分类总表"）
3. `agent.py`（AgentSpec / Profile / Capability / AgentSelector / Resources / Probe / RetryPolicy）
4. `pipeline.py`（Pipeline / PipelineRun / Stage / Compensation / GlobalSpec / SecretRef / ParameterDef）
5. `state.py`（WorkflowState / StageOutput / ArtifactRef / Checkpoint / ProgressInfo）

**DoD**
- [ ] 所有类型 `from __future__ import annotations` + Pydantic v2 BaseModel
- [ ] 不 import temporalio / requests / 任何 IO
- [ ] `mypy --strict src/orchestra/domain` 通过
- [ ] `pytest tests/unit/test_domain_*.py` 全绿（每个文件 ≥3 个测试：构造 / 序列化 / 默认值）
- [ ] 所有错误类有 `is_retryable: ClassVar[bool]`

**反模式（坚决拒绝）**
- 在 dataclass 里写业务逻辑（解析 / 校验 / 转换）
- 让 `errors.py` import `observability/`（"自动写日志"）

---

## P1 — schema（2-3 PR）

**目标**：YAML → Pipeline domain 对象的完整管线就绪。

### PR 1：parser + validator（结合 JSON Schema）
- `parser.py`：PyYAML safe_load → dict → Pydantic Pipeline
- `validator.py`：jsonschema 2020-12 校验（载入 `schema/pipeline.schema.json`）
- DoD：`tests/unit/test_validator.py` 用 `examples/*.yaml` 全部通过 + 故意构造 5 条错误用例全部捕获

### PR 2：dag.py
- Kahn 拓扑排序、环检测、孤儿节点、子流水线递归
- DoD：`tests/unit/test_dag.py` 覆盖 10 种 DAG 模式（参见 design.md）

### PR 3：jsonpath + expr + template
- `jsonpath.py`：路径解析 + 写隔离校验
- `expr.py`：CEL 沙箱（用 cel-python），condition 求值
- `template.py`：`{{ params.* }}` / `{{ inputs.* }}` 渲染（注意：渲染必须发生在 schema 校验**之后**）
- DoD：表达式沙箱安全测试（拒绝 `__import__`、`eval`、文件 IO）

**全 P1 完成判据**：`orchestra validate examples/game-dev.pipeline.yaml` 命令可跑通（即便 cli 还没写，可暴露 Python entrypoint）。

---

## P2 — state / adapters / observability（3-4 PR，可并行）

### PR：observability（先做，下游都用）
- `metrics.py`：Prometheus 指标定义（参考 design.md 表）
- `tracing.py`：OTel SpanFactory + 上下文传播
- `logging.py`：structlog JSON + redact 函数
- `audit.py`：SQLite/Postgres 审计表 schema + writer
- DoD：mock prometheus_client，断言每个 metric 都被 increment；`logging.redact()` 拒绝泄露 API key / JWT

### PR：state
- `idempotency.py`：Redis（asyncio）+ SQLite（aiosqlite）双后端，统一 `get/put` 接口
- `artifact_store.py`：local 实现先行（s3/oss 留 NotImplementedError）
- `codec.py`：`EncryptingCodec` 实现 AES-256-GCM；`is_sensitive` 看字段名 / 显式标签
- `store.py`：JSONPath 读写 + 写隔离
- DoD：每个文件单测；codec encrypt → decrypt 往返；写隔离 violation 抛 `SchemaViolation`

### PR：adapters
- `base.py`：`AgentAdapter` Protocol（**冻结**，后续扩展只能新增文件）
- `mock.py`：单元测试用，可配置 success / fail / cancel / timeout 行为
- `sandbox.py`：tool 白名单 + 参数 sanitize（防路径穿越 `../`、防 prompt 注入边界标记）
- `mcp.py`：真实 MCP 客户端（HTTPX async，传 traceparent header）
- `registry.py`：从 `config/profiles.yaml` 加载 → profile name → AgentAdapter 工厂
- DoD：`test_adapter_mock.py` 覆盖 5 种行为；`mcp.py` 用 dummy MCP server 跑通契约测试

---

## P3 — activities（2-3 PR）

> ⚠️ 必须配合 `temporal-activity` 子 agent 写。所有 Activity 三件套（心跳/幂等/取消）一个不能少。

### PR：agent_task.py（核心 Activity）
- `execute_agent_task(input: AgentTaskInput) -> AgentTaskOutput`
- 三件套 + LLM token 计量 + 错误分类
- DoD：mock adapter 跑通正常 / 取消 / 重试 / 幂等命中四个场景

### PR：artifact + notification + audit + compensation
- `artifact.py`：落盘 + sha256 + 清理策略
- `notification.py`：飞书 webhook（其他渠道占位 NotImplementedError）
- `audit.py`：调 `observability.audit` 写入审计表的 Activity 包装
- `compensation.py`：Saga 反向调用模板
- DoD：所有 Activity 都有 mock adapter 单测；`pytest -m unit` 全绿

---

## P4 — workflows（3-4 PR，最难、必须谨慎）

> ⚠️ 必须配合 `temporal-workflow` 子 agent 写。每个 PR 后必跑 `replay-guardian` 子 agent。

### PR 1：pipeline_workflow.py 主框架
- `PipelineWorkflow.run(spec: Pipeline, params: dict)`
- DAG 调度（按拓扑顺序派发 Activity）
- 全局 State 维护
- 必带 `# DETERMINISM REQUIRED — see CLAUDE.md §3` 文件头
- DoD：`tests/integration/test_workflow_basic.py` 跑通 `minimal.pipeline.yaml`

### PR 2：signals + queries + updates
- `signals.py`：cancel / pause / resume / override
- `queries.py`：get_progress / get_dag_status / get_approval_status / get_state_size
- `updates.py`：approve / reject（带校验 + 返回值）
- DoD：CLI 还没写，但能用 `temporal CLI` 手工发 signal/query/update 通过

### PR 3：condition + parallel + aggregateStrategy
- 实现 condition 跳过语义（SKIPPED 级联）
- 实现 6 种 aggregateStrategy
- DoD：`test_workflow_branching.py` + `test_workflow_parallel.py` 全绿

### PR 4：child_workflows + dynamic + loop + compensation
- `execute_child_workflow` 包装
- `dynamic.for_each` 展开
- `loop` 受限循环
- 失败 → Saga 反向调用
- DoD：四个高级场景各 1 个集成测试通过

**全 P4 完成判据**：`game-dev.pipeline.yaml` 用 mock adapter 跑通端到端（不含真 LLM）。

---

## P5 — worker（1-2 PR）

### PR：lifecycle + main + registry
- `lifecycle.py`：startup probe（MCP/LLM/工具自检）+ SIGTERM 优雅关闭
- `registry.py`：注册 Workflow + Activity + PayloadCodec（顺序很重要）
- `main.py`：`python -m orchestra.worker.main` 可跑；读 `PROFILE_NAME` 加载 profile
- DoD：
  - `docker compose up -d temporal-server worker-walnut` 后，`/metrics` 端口可访问，Worker 在 Temporal UI 中显示 Active
  - SIGTERM 后任务能 failover（chaos 测试 `test_kill_worker.py`）

---

## P6 — cli（4-5 PR）

按 design.md "CLI 设计" 章节实现。建议分组：

### PR 1：基础（main + output + validate + dry-run）
### PR 2：生命周期（submit + status + cancel）
### PR 3：交互（approve + reject + signal + re-run）
### PR 4：运维（agents + schedule + logs + health）
### PR 5：调试（replay + inspect + chaos + diff + version）

**每个 PR DoD**：`CliRunner` mock Temporal Client，覆盖参数解析 / 输出格式 / 错误处理。

---

## P7 — 部署联调 + Replay fixture（持续）

- 用 `examples/minimal.pipeline.yaml` 跑端到端 → 录第一个 replay fixture
- 用 `examples/game-dev.pipeline.yaml` 跑端到端（含审批） → 录第二个 fixture
- Grafana dashboard 接真数据，校对 metric 名称
- 跑 chaos 测试（kill agent / kill temporal / corrupt state）
- 跑 load 测试（1000 stage 并发 mock）

---

## 何时停下来重审

- 跨模块出现循环依赖 → 立即停，重看 `CLAUDE.md` §5 import 顺序
- domain 类型字段需要新增 → 触发 schema-keeper 子 agent，bump 还是兼容？
- Workflow 改了一行后 replay 测试挂了 → 触发 replay-guardian 子 agent
- 新增 metric / span / audit 字段 → 触发 observability 子 agent
- 引入新 agent profile → 触发 mcp-adapter 子 agent

## 不要做的（避免范围蔓延）

- 写 server/（Phase 5+）
- 写 webhook 触发器（未来）
- 写 K8s Operator（不在 Phase 1-3）
- 在 P5 之前写 cli（worker 没起来 cli 没意义）
- 引入新依赖前不先和 `pyproject.toml` 对齐

---

## 附：每模块文件粒度建议

| 模块 | 文件数上限 | 单文件行数上限 | 理由 |
|---|---|---|---|
| domain | 5 | 200 | 仅类型 |
| schema | 6 | 250 | parser / validator / dag / jsonpath / expr / template |
| state | 4 | 250 | store / artifact / codec / idempotency |
| adapters | 5 | 200 | base / mcp / mock / sandbox / registry |
| observability | 4 | 200 | metrics / tracing / audit / logging |
| activities | 5 | 250 | 一个 Activity 一个文件 |
| workflows | 5 | 400 | pipeline_workflow 可破例到 400 |
| worker | 3 | 200 | main / registry / lifecycle |
| cli/commands | 17 | 100 | 一个子命令一个文件 |

超过上限请拆。
