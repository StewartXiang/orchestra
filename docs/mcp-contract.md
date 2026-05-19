# MCP Agent Contract — Orchestra ↔ Agent 通信协议

Agent 实现此协议后即可被 Orchestra 编排。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查，返回 `{"status": "healthy"}` |
| `GET` | `/capabilities` | 返回 Agent 能力声明 |
| `GET` | `/metrics` | 返回运行时指标（可选） |
| `POST` | `/execute` | 执行任务（核心端点） |
| `POST` | `/cancel` | 取消任务（可选） |

## Request: POST /execute

Orchestra 发送：

```json
{
  "task_id": "wf-abc123/code",
  "stage": "code",
  "role": "developer",
  "tools": ["file_read", "file_write"],
  "input": <any>,
  "prompt": "你是游戏开发者。用 Godot 实现以下功能...\n\n输入：\n...",
  "output_schema": {
    "type": "object",
    "required": ["patch"],
    "properties": {"patch": {"type": "string"}}
  },
  "response_tool": {
    "name": "submit_result",
    "description": "完成任务后调用此 tool 提交最终结果。参数必须严格匹配 output_schema。",
    "parameters": <output_schema>
  },
  "resume_from": {
    "step": "compiling",
    "progress": 45,
    "data": {}
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `task_id` | ✓ | 幂等键，重试时相同 |
| `stage` | ✓ | Stage 名称 |
| `input` | ✓ | 任务输入数据 |
| `prompt` | | Stage 级 prompt（已模板展开） |
| `output_schema` | | 期望输出 JSON Schema |
| `response_tool` | | 结构化输出 tool 定义 |
| `resume_from` | | 断点续传信息（重试时） |

## Response: POST /execute

Agent 必须返回以下结构之一：

### 格式 1：标准输出（推荐）

```json
{
  "output": {"patch": "def hello(): pass"},
  "tokens_consumed": 420,
  "cost_usd": 0.003,
  "metadata": {"files_changed": ["main.gd"]}
}
```

### 格式 2：tool-call 输出（有 response_tool 时推荐）

```json
{
  "tool_calls": [{
    "name": "submit_result",
    "arguments": {"verdict": "pass", "issues": []}
  }],
  "tokens_consumed": 150,
  "cost_usd": 0.001
}
```

Orchestra 自动识别 `tool_calls` / `tool_use` / `tool_call` 字段，提取 `submit_result` 的 `arguments`。

### 格式 3：兼容旧格式

```json
{
  "result": {"patch": "..."},
  "tokens_consumed": 100
}
```

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `output` | 是* | any | 任务执行结果（*三种格式之一） |
| `tokens_consumed` | | int | LLM token 消耗 |
| `cost_usd` | | float | LLM 调用费用（USD） |
| `duration_seconds` | | float | 执行时长（秒） |
| `metadata` | | object | 额外元数据 |
| `artifacts` | | array | 产出物引用列表 |

## Response: GET /capabilities

```json
{
  "role": "developer",
  "capabilities": ["python", "godot", "gdscript"],
  "tools": ["godot_edit", "file_read", "file_write", "git_commit"],
  "model": "deepseek-v4-pro",
  "version": "0.1.0"
}
```

## Response: GET /health

```json
{"status": "healthy", "role": "developer"}
```

## 错误处理

Agent 错误通过 HTTP 状态码表达：

| 状态码 | Orchestra 处理 |
|--------|---------------|
| 200 | 成功 |
| 401/403 | → `AuthError`（不重试） |
| 429 | → `RateLimited`（退避重试） |
| 5xx | → `TransientError`（重试） |
| 4xx | → `TransientError`（重试） |
| 超时 | → `Timeout`（重试） |

## 实现参考

内置于 `scripts/demo_agent.py` 的 Demo Agent 完整实现了此协议，可作为参考实现。
