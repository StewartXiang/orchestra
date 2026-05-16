# adapters/

## 职责
Worker 与 Agent 之间的通信抽象层。统一封装 MCP 协议调用、健康检查、能力发现、取消、心跳传递。

## 关键文件

| 文件 | 责任 |
|---|---|
| `base.py` | `AgentAdapter` Protocol（**唯一公开契约**，扩展 Adapter 不改这个文件） |
| `mcp.py` | MCP 协议实现 |
| `mock.py` | 单元测试用 mock，每次扩展 Adapter 必须同步更新 |
| `sandbox.py` | 工具调用白名单 + 参数 sanitize（防 prompt 注入 / 路径穿越） |
| `registry.py` | profile name → AgentAdapter 实例工厂 |

## AgentAdapter Protocol

```python
class AgentAdapter(Protocol):
    async def execute_task(
        self, task: TaskInput,
        on_heartbeat: Callable[[ProgressInfo], None] | None = None,
        resume_from: Checkpoint | None = None,
    ) -> TaskOutput: ...
    async def check_health(self) -> HealthStatus: ...
    async def cancel_task(self, task_id: str, grace_period: timedelta) -> None: ...
    async def get_capabilities(self) -> AgentCapabilities: ...
    async def get_metrics(self) -> AgentMetrics: ...
```

## 边界
- 不读流水线 State / 不写流水线产物 / 不发指标
- Adapter 只负责"我能调到 Agent 吗 + 调用得到什么"
- State 流转、产物落盘、指标在 `activities/` 完成

## 测试策略
- `tests/unit/test_adapter_*.py`：mock 实现的契约测试
- `tests/integration/test_adapter_mcp.py`：跑一个真 MCP server fixture

## 常见陷阱
- 改 `base.py` 加方法 → 破坏 Protocol；新增能力请用 mixin 或子 Protocol
- 在 `mcp.py` 里 hardcode tool 名 → 走 `sandbox.py` 白名单
- 取消时不释放 LLM 配额 → 必须 cancel 上游 HTTP client
