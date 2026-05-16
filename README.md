# Agent Orchestra

像 Kubernetes 管理 Pod 一样管理 AI Agent。

声明式 YAML 定义流水线 → Temporal 执行 → MCP 通信 Agent → Prometheus/OTel 可观测。

## 现状

骨架阶段。源码尚未填充。详见 `docs/`：

- [`docs/requirements.md`](docs/requirements.md) — 需求与目标
- [`docs/design.md`](docs/design.md) — DSL / Schema / 数据流
- [`docs/architecture.md`](docs/architecture.md) — 架构与部署

## 快速上手（规划）

```bash
# 启动整套
docker compose -f deploy/docker-compose.yml up -d

# 校验流水线
orchestra validate examples/minimal.pipeline.yaml

# 提交执行
orchestra submit examples/minimal.pipeline.yaml --param target_env=dev

# 查看
orchestra status --watch
```

## 目录

```
docs/        三份核心文档（requirements / design / architecture）
schema/      JSON Schema（pipeline / pipeline-run / agent-profile）
config/      profiles / capabilities 词表
examples/    示例 pipeline.yaml + values 文件
deploy/      docker-compose / prometheus / grafana / otel
src/         Python 实现（domain / schema / workflows / activities / adapters / state / observability / worker / cli）
tests/       单元 / 集成 / replay / chaos / load
runbook/     故障处置 SOP
.claude/     Claude Code 配置（settings + 特化子 agent）
CLAUDE.md    项目宪法（领域术语 / 确定性铁律 / 幂等铁律）
```

## 开发约定

阅读 [`CLAUDE.md`](CLAUDE.md) 之前不要写第一行代码。
