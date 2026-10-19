# tests/replay/fixtures/

## 治理规范

每个 fixture 是一段生产或集成测试录制的 Temporal Workflow History（JSON），用于 `WorkflowReplayer` 验证代码改动不破坏已有运行中的 Workflow。

## 命名
`<feature>_<scenario>.json`，例如：
- `linear_chain_happy.json`
- `parallel_fanout_partial_failure.json`
- `approval_timeout_escalate.json`
- `dynamic_for_each_50_items.json`
- `compensation_reverse.json`

## 头部注释（写在 JSON 同名 .md 中）
```
# linear_chain_happy.json
covers: 最简线性链 happy path（design → code → test）
captured: 2026-05-16
workflow: PipelineWorkflow v1.2.0
```

## 维护规则
- 每次大改 Workflow 必须**新增**至少 1 个 fixture（不是替换）
- 旧 fixture 不要轻易删；至少保留 3 个版本作为兼容性回归
- fixture 体积 > 1MB → 用 git LFS

## 来源
- 生产：`temporal workflow show -w <id> --output json > fixtures/<name>.json`
- 集成测试录制：跑 `tests/integration/test_workflow_*.py` 时设环境变量 `RECORD_FIXTURES=1`
- 脚本录制：`python scripts/record_fixtures.py`（in-process，无需 Server）

## 两类 fixture
| 前缀 | 来源 | 是否入库 |
|---|---|---|
| `synthetic_*.json` | mock Adapter 在 in-process 环境录制，无 PII | ✅ 随仓库提交，供 CI replay 回归 |
| 其他 `*.json` | 生产 / 真实 Server 录制，可能含敏感数据 | ❌ 被 .gitignore 忽略，走 git LFS |

> Replayer **必须**复用 `orchestra.worker.registry.build_workflow_runner()` 与
> Pydantic DataConverter，否则沙箱 passthrough 不一致会误报
> `NondeterminismError`（见 runbook/02-replay-failure.md）。

## 当前 fixture
- `synthetic_minimal_happy.json` — minimal pipeline（code → test）happy path
- `synthetic_review_gate_pass.json` — reviewGate 首轮 verdict=pass
- `synthetic_review_gate_loop.json` — reviewGate fail → 修复 → 重测 → pass

## 隐私
- 录制前必须 redact PII / 密钥 / token
- 生产录制用 `scripts/redact_history.py`（待实现）清洗后入 LFS
