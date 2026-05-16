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

## 隐私
- 录制前必须 redact PII / 密钥 / token
- 用 `scripts/redact_history.py`（待实现）清洗

## 待录制 fixture

以下 fixture 需要在真实 Temporal Server 上录制：

- `linear_happy.json` : minimal pipeline (2 stages) - fixture-minimal-e15dd095

录制方法:
  1. `docker compose -f deploy/docker-compose.yml up -d`
  2. `orchestra submit examples/minimal.pipeline.yaml --param task="hello"`
  3. `temporal workflow show --workflow-id <id> --output json > tests/replay/fixtures/linear_happy.json`
