# domain/

## 职责
纯领域模型与类型。零外部依赖（除 stdlib + pydantic + 类型 stub）。被所有上层模块依赖，但**不依赖任何 IO/Temporal/网络**。

## 关键类型

| 文件 | 类型 | 说明 |
|---|---|---|
| `pipeline.py` | `Pipeline` / `Stage` / `PipelineRun` / `Compensation` | YAML 解析后的不可变值对象 |
| `agent.py` | `AgentSpec` / `Profile` / `Capability` / `Role` / `AgentSelector` | Agent 规格与选择器 |
| `state.py` | `WorkflowState` / `StageOutput` / `Artifact` | Workflow 全局 State 契约 |
| `errors.py` | `OrchestraError` / `AuthError` / `ToolNotAllowed` / ... | 错误分类基类 |
| `enums.py` | `Phase` / `AggregateStrategy` / `OutputStorage` / `Priority` / `BackoffKind` | 全部枚举 |

## 边界
- **不允许** `import temporalio` / `import requests` / `import sqlite3`
- **不允许**有副作用的函数（不写文件、不发请求、不读环境变量）
- 允许 `from pydantic import BaseModel`（仅类型定义）
- 错误类必须有类属性 `is_retryable: bool`

## 测试策略
`tests/unit/test_domain_*.py`：纯构造 / 序列化 / 比较测试，不需 fixtures。

## 常见陷阱
- 把"默认值"写在 dataclass 里 → 与 schema 默认值漂移；保持 `Optional` + 由 `schema.parser` 应用 schema 默认值
- 在错误类里塞 IO（如自动写日志）→ 违反纯净性，留给 `observability` 处理
