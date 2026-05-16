# activities/

## 职责
Temporal Activity 实现。所有副作用（调 Agent / 写文件 / 发请求 / 写审计）的唯一容身之处。

## 关键文件

| 文件 | 责任 |
|---|---|
| `agent_task.py` | 调 Agent 的核心 Activity（execute_agent_task） |
| `compensation.py` | Saga 补偿动作 |
| `artifact.py` | 产出物落盘 + 校验和（local / s3 / oss） |
| `notification.py` | 飞书 / 邮件 / Slack |
| `audit.py` | 审计日志写入（独立 SQLite/Postgres 表） |

## 三件套铁律

每个 `@activity.defn` 第一行 `activity.heartbeat(...)`，主体内：

1. **幂等查**：`key = info.workflow_id + info.activity_id`，命中 cache 直接 return
2. **执行**：周期心跳（≤ heartbeat_timeout / 3）+ `activity.is_cancelled()` 检查
3. **幂等写**：执行成功后写 idempotency store，TTL 24h

详见 `CLAUDE.md` §4 + `temporal-activity` 子 agent 提示。

## 错误约定
- 抛 `orchestra.domain.errors.AuthError / ToolNotAllowed / InvalidInput / SchemaViolation / ApprovalRejected / BudgetExceeded` → 自动 nonRetryable
- 抛 `TransientError` 或默认异常 → 走 RetryPolicy
- 用 Temporal 原生 `ApplicationError(non_retryable=True)` 也可，但建议统一用 `orchestra.domain.errors`

## 边界
- 这里**可以** import `state/` / `adapters/` / `observability/` / `domain/`
- 不要直接 import `workflows/`（会循环依赖且违反分层）

## 测试策略
- `tests/unit/test_activity_*.py`：用 mock adapter，覆盖正常 / 取消 / 重试 / 幂等命中
- `tests/integration/`：跑真实 Worker（开发模式 Temporal）

## 常见陷阱
- 幂等 key 加上 `attempt` → 重试时不命中 cache，破坏幂等
- 心跳间隔 > heartbeat_timeout → 永远超时
- 异常裸 `raise` 不带分类 → 全部走 retry，浪费配额
- 在 Activity 里直接 `print` → 用 `observability.logging`
