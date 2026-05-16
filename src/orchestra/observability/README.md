# observability/

## 职责
Metrics / Tracing / Audit / 结构化日志的统一出口。**所有埋点经此处**，避免散落代码各处。

## 关键文件

| 文件 | 责任 |
|---|---|
| `metrics.py` | Prometheus 指标定义（Counter / Histogram / Gauge），统一 subsystem_name_unit 命名 |
| `tracing.py` | OTel span 工厂 + Workflow → Activity → MCP → LLM 上下文传播 |
| `audit.py` | 审计日志 schema + writer（单独 SQLite/Postgres 表） |
| `logging.py` | structlog JSON 日志 + redact 规则 |

## 命名规范

`<subsystem>_<name>_<unit>`：
- 单位：`_total`(counter) / `_seconds`(histogram time) / `_bytes` / `_ratio` / 无后缀(gauge)
- subsystem 词表：`pipeline / stage / agent / workflow / task_queue / approval / llm / replay`

## Span 命名
`<subsystem>.<operation>`：`pipeline.run` / `stage.execute` / `agent.call` / `mcp.tool.<name>` / `llm.<provider>.chat`

## 边界
- **Workflow 不能直接 import 本目录**（违反确定性）。Workflow 暴露指标走：
  - `workflow.set_search_attributes(...)` → Temporal Visibility
  - 通过 Activity 上报
  - SDK 提供的 `workflow.metric_meter()`
- Activity / Adapter / CLI 可直接 import

## 测试策略
- `tests/unit/test_metrics.py`：mock prometheus_client，断言计数器 increment
- `tests/unit/test_logging.py`：redact 用例（API key / token / JWT）
- `tests/unit/test_audit.py`：审计字段必填 / SQL schema 兼容

## 常见陷阱
- 给 Counter 加 cardinality 爆炸的 label（如 `workflow_id`） → Prometheus OOM
- 跨进程 trace context 不传 → span 断裂；所有 Activity input 必须带 traceparent
- 日志直接打印 LLM prompt → API key 泄露；必经 `redact()`
