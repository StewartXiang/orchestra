# cli/

## 职责
`orchestra` 命令行客户端。和 Temporal Server 交互（提交 Workflow、Signal、Query、Update、Replay）。

## 关键文件

| 文件 | 责任 |
|---|---|
| `main.py` | Click 入口（`orchestra ...`） |
| `output.py` | json / yaml / table 多格式输出 |
| `commands/*.py` | 一个文件一个子命令（保持 ≤ 100 行） |

## 子命令清单

| 命令 | 文件 | 概述 |
|---|---|---|
| `validate` | validate.py | 静态校验（schema + DAG） |
| `dry-run` | dry_run.py | 渲染 DAG 拓扑（dot/mermaid），可选 mock |
| `submit` | submit.py | 提交流水线（带 --param / --idempotency-key） |
| `status` | status.py | 列表 / 详情 / --watch / --query |
| `cancel` | cancel.py | 优雅取消 / --force |
| `approve` / `reject` | approve.py / reject.py | 审批节点（Update API） |
| `re-run` | re_run.py | 从某 stage 重跑 / 重置 state |
| `signal` | signal.py | 发任意 signal |
| `agents` | agents.py | 列 agent / drain / resume |
| `schedule` | schedule.py | create/list/pause/resume/trigger/delete |
| `logs` | logs.py | 拉日志（按 pipeline / stage） |
| `replay` | replay.py | 跑 WorkflowReplayer 检测兼容 |
| `inspect` | inspect.py | 导出 Event History (JSON) |
| `chaos` | chaos.py | 故障注入（kill-agent / network-partition / corrupt-state） |
| `health` | health.py | 集群体检 |

## 约定
- 子命令文件 ≤ 100 行；复杂逻辑下沉到 `domain` / 调 `state` / 调 Temporal Client
- Click `--output` 全局参数，走 `output.py` 渲染
- 有副作用的命令（cancel/approve/re-run）默认要求 `--yes` 或 TTY 确认
- `--idempotency-key` 提交流水线时透传到 Temporal `start_workflow(id=...)`

## 测试策略
- `tests/unit/test_cli_*.py`：Click `CliRunner` + mock Temporal Client
- 重点：参数解析正确 / 输出格式正确 / 异常用户友好

## 常见陷阱
- 直接 print 表格 → 用 rich + `output.py`
- 把 Temporal Client 全局单例 → 测试难 mock；通过 ctx 注入
- 子命令调用之前不校验 schema → 让用户在 status 时才发现错；submit 必先 validate
