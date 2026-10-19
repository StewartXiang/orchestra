# Changelog

## [Unreleased]

### Fixed
- **condition 表达式在 cel-python ≥0.4 全部失效**：`CELBool/CELList/CELMap` 已改名为
  `BoolType/ListType/MapType`，导致 `tests/unit/test_expr.py` 4 例 + 依赖 condition
  的集成用例失败。`schema/expr.py` 改为双版本类型解析，并支持 DSL 文档承诺的
  `and`/`or`/`not`（归一为 CEL `&&`/`||`/`!`，跳过字符串字面量）。`null` 保持
  Python `None` 语义。（`pyproject.toml` 同步加 `cel-python<0.6` 上界）
- **Replay 误报非确定性**：`Replayer` 默认沙箱未 passthrough opentelemetry 等模块，
  与 Worker 不一致，任何录制 history 回放都会 `NondeterminismError`。新增
  `worker.registry.build_workflow_runner()`，Worker / Replayer / 录制脚本共用。
- **含连字符的 Stage 名静默丢状态**：`schema/jsonpath.py` 的 `parse_path` 只接受
  `[A-Za-z_]\w*` 段名，`$.quality-gate` 解析为空 → `_set_state` 异常被吞。现支持
  `$.quality-gate` 与 `$['quoted key']`，并在写失败时记 `workflow.logger.warning`。
- **ReviewIssue.area 与文档不一致**：design.md / `config/review-schema.yaml` 的
  required 列表不含 `area`，Pydantic 却必填，按文档产出的 review 结果会解析失败。
  改为可选。
- **手动审批不生效**：`_get_stage_approval` 恒返回 `None`，`approve` update 永远不把
  状态置为 approved（只有超时自动审批与 `ou_`/`dev_` 占位符自动通过能走通）。
  删除该死方法，`approve` 直接调用 `_check_approval_policy`。
- **若干 CLI 子命令调用不存在的 SDK API**（运行时报错，此前无测试覆盖）：
  `schedule` 的 `Schedule*` 改从 `temporalio.client` 导入；`replay` 改用
  `temporalio.worker.Replayer` + `WorkflowHistory`；`re-run` 改用
  `workflow_service.reset_workflow_execution`（`WorkflowHandle` 无 `reset`）；
  `health` 去掉不存在的 `ServiceClient.close()`；`status` 去掉对 async iterator 的
  `await`。
- **加密 Codec 的 metadata key 类型错误**：`_ENCRYPTION_METADATA_KEY` 为 `bytes`，
  而 `Payload.metadata` 是 `str → bytes`，加密路径实际会抛错；改为 `"encoding"`。
- **condition 未声明变量报错语义**（mypy/严格化顺带）：`validator.py` 中 except
  变量 `e` 与循环变量重名。
- `mypy --strict src` 由 **85 errors → 0**（新增 `PipelineWorkflowBase` 类型基类、
  补齐泛型参数与返回类型、修正 temporalio API、删除陈旧 `# type: ignore`）。
- **`agent_resolver` 取消路径 `UnboundLocalError`**：`CancelledError` 只在循环前的
  `if activity.is_cancelled():` 块内 import，循环内再次 `raise` 时该局部名未绑定。
  改为模块顶层 import。
- 清理未使用 import（`child_workflows` / `signals` / `control_flow`）。

### Added
- `tests/integration/test_workflow_review_gate.py`：Review Gate 三条路径
  （pass / fail→修复→重测→pass / maxIterations 耗尽失败）。
- `tests/unit/test_workflow_approval_update.py`：手动 approve/reject update 回归
  （集成测试用 time-skipping，无法稳定测交互式 Update，故用单元测试覆盖）。
- `tests/unit/test_workflow_structure.py`：守卫 Mixin 由沙箱加载、workflow 文件 ≤400 行。
- `tests/unit/test_cli_commands.py`：19 个 CLI 子命令的 CliRunner + mock 覆盖
  （cli 区域 49.1% → 97.7%，并回归了 schedule/replay/re-run/health/status 的 SDK API 修正）。
- `tests/unit/test_adapter_mcp.py`：MCPAdapter 响应契约 / 错误映射 / 探针
  （httpx.MockTransport，不连真实服务）。
- `tests/unit/test_observability.py`：logging redact / tracing / metrics / audit。
- `tests/unit/test_worker_lifecycle.py` + `tests/unit/test_activities_extra.py`：
  worker 生命周期与 startup probe、activities 幂等/错误分支（两者区域 0–59% → 100%）。
- Replay fixtures：`synthetic_minimal_happy` / `synthetic_review_gate_pass` /
  `synthetic_review_gate_loop`（mock 录制、无 PII，随仓库提交）。
- `MockAgentAdapter.output_sequence` + `build_registry(mock_output_sequences=...)`：
  支持同一 Agent 多轮返回不同输出。

### Changed
- `workflows/pipeline_workflow.py` 由 1185 行拆为 8 个模块（本文件 389 行），
  满足 CLAUDE.md §5「单文件 ≤400 行」：新增 `pipeline_base` / `pipeline_types` /
  `stage_execution` / `dynamic_execution` / `review_gate` / `control_flow` /
  `workflow_utils`。
- `ruff check` 1549 → 0、`ruff format` 统一 125 个文件（CI lint/format 门禁转绿）。
- **CI 覆盖率口径修正**：原 `pytest tests/unit --cov-fail-under=80` 只量单元测试，
  而 `workflows`（780 行）主要由集成测试覆盖，unit-only 只有 14–28%，口径与测试
  分层错配、长期无法达标（实测 unit-only 57%）。改为 `tests/unit tests/integration`
  一起量（chaos/load 不计入），阈值仍为 80%；同时补齐 CLI/观测/适配器/worker/activities
  单元测试，整体覆盖率 **67.9% → 89.1%**。排除项仅限 `if TYPE_CHECKING:`、
  `raise NotImplementedError`（抽象桩）、`if __name__ == "__main__":` 等不可执行代码。

### Tests
- 收集总数 305：unit 266 / integration 26 / replay 3 / chaos 7 / load 3
  （另有 2 skipped、1 xfail）。（0.1.0 条目中的 "108 collected" 为误记。）

## [0.1.0] — 2026-05-19

### Initial Release — 桃儿-core

The first public release of Agent Orchestra, the declarative AI Agent pipeline engine.

**Core Engine:**
- Declarative YAML DSL for agent pipelines (Kubernetes CRD-inspired)
- Temporal-powered execution kernel with event history replay
- DAG topology scheduling (Kahn algorithm, cycle detection)
- CEL expression engine for stage conditions
- JSONPath data flow between stages

**Pipeline Features:**
- Parallel fan-out with 6 aggregation strategies (all/any/first/merge/vote/quorum)
- Conditional branching (pass → SKIPPED cascade, fail → compensation)
- Human-in-the-loop approval gates (any/all/quorum policy, timeout)
- Dynamic `for_each` stage generation (maxParallel limiter)
- Saga compensation for failure rollback
- Parameterized pipelines with values files

**Agent Management:**
- 9 built-in agent profiles (walnut/chestnut/almond/coconut/cherry/mango/strawberry/blueberry/grape)
- MCP protocol adapter (Model Context Protocol)
- Agent health probing (3-layer: startup/liveness/readiness)
- Capability-based agent routing (agentSelector)
- Max concurrency task queue isolation

**CLI (19 commands):**
- `validate`, `dry-run`, `submit`, `status --watch`
- `approve`/`reject` for human gates
- `cancel`, `re-run`, `replay`, `signal`
- `schedule`, `agents`, `logs`, `health`
- `inspect`, `list-pipelines`, `chaos`, `init`

**Observability:**
- Prometheus metrics (pipeline/stage/agent/llm-token)
- Grafana dashboards (pipeline, agent, system health)
- OpenTelemetry tracing with context propagation
- Structured JSON logging (structlog)
- Audit trail (SQLite)

**Operations:**
- Docker Compose (demo + production topologies)
- Prometheus alert rules
- Runbook SOPs (8 failure scenarios)
- CI/CD pipeline (lint, schema, unit, replay, integration, deploy-config)

**Tests: 108 collected**
- Unit: domain, schema (parser/validator/DAG/expr/jsonpath/template), activity, adapter
- Integration: basic, branching, approval, dynamic, game-dev pipelines
- Replay: compatibility fixtures
- Chaos: kill-agent, kill-temporal, corrupt-state
- Load: throughput baseline

**Notable:**
- ~10,500 lines of Python
- ~5,500 lines of documentation
- 7 commits of considered, tight-coupling-minimized architecture
