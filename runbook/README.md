# Runbook 总索引

故障处置 SOP。**遵循"先稳态、再根因、后改进"**。

| 编号 | 标题 | 触发告警 | P 级 |
|---|---|---|---|
| [01](01-agent-heartbeat-flap.md) | Agent 心跳频繁超时 | `AgentDown` | P2 |
| [02](02-replay-failure.md) | Workflow Replay 失败 | `WorkflowReplayFailure` | P1 |
| [03](03-task-queue-backlog.md) | Task Queue 积压 | `TaskQueueBacklog` | P2 |
| [04](04-approval-pending.md) | 审批长时间未响应 | `ApprovalPending` | P3 |
| [05](05-state-too-large.md) | State 超过 2MB 限制 | `EventHistoryNearLimit` / `StateOversize` | P1 |
| [06](06-llm-quota-exhausted.md) | LLM 配额耗尽 / 429 | `LLMCostBudgetExceeded` | P2 |
| [07](07-misfired-pipeline.md) | 误提交错误流水线 | 人工触发 | P1 |

## 通用应急
- 取消有副作用的流水线：`orchestra cancel <id>` → 看 `sideEffects` 字段评估
- 强行终止：`orchestra cancel <id> --force`（跳过 cleanup）
- 集群体检：`orchestra health`
- Web UI：http://localhost:8080
- Grafana：http://localhost:3000
