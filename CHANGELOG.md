# Changelog

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
