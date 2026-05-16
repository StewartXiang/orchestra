"""Orchestra 错误层次。

约定：
- 所有自定义错误继承 ``OrchestraError``
- 类属性 ``is_retryable: ClassVar[bool]`` 决定是否进入 RetryPolicy
- nonRetryable 错误名同步出现在 Pipeline YAML ``retry.nonRetryableErrors`` 默认列表
- Activity 中抛出时，引擎会转为 Temporal ``ApplicationError(non_retryable=...)``

详见 docs/design.md "错误分类总表"。
"""

from __future__ import annotations

from typing import Any, ClassVar


class OrchestraError(Exception):
    """所有 Orchestra 自定义错误的基类。"""

    is_retryable: ClassVar[bool] = True
    error_code: ClassVar[str] = "OrchestraError"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


# ---------- nonRetryable 错误（永久性，立即失败） ----------

class AuthError(OrchestraError):
    """认证 / 授权失败：API Key 失效、Token 过期、权限不足。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "AuthError"


class ToolNotAllowed(OrchestraError):
    """Agent 试图调用未在 ``tools`` 白名单中的工具。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "ToolNotAllowed"


class InvalidInput(OrchestraError):
    """输入数据 schema 校验失败 / 参数非法。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "InvalidInput"


class SchemaViolation(OrchestraError):
    """输出 schema 不符 / State 写隔离违规。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "SchemaViolation"


class ApprovalRejected(OrchestraError):
    """审批节点被拒绝。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "ApprovalRejected"


class BudgetExceeded(OrchestraError):
    """LLM token 配额或成本预算超限。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "BudgetExceeded"


class ConfigurationError(OrchestraError):
    """配置不一致：profile 引用未定义、capability 词表外、超时关系不合法。"""
    is_retryable: ClassVar[bool] = False
    error_code: ClassVar[str] = "ConfigurationError"


# ---------- retryable 错误（瞬态，走 RetryPolicy） ----------

class TransientError(OrchestraError):
    """瞬态错误总称：网络抖动、临时 5xx、临时不可达。"""
    is_retryable: ClassVar[bool] = True
    error_code: ClassVar[str] = "TransientError"


class RateLimited(OrchestraError):
    """上游限流（如 LLM 429）。退避间隔需更长。"""
    is_retryable: ClassVar[bool] = True
    error_code: ClassVar[str] = "RateLimited"


class TimeoutError(OrchestraError):
    """超时：Activity / 工具调用 / LLM 响应。"""
    is_retryable: ClassVar[bool] = True
    error_code: ClassVar[str] = "TimeoutError"


class MCPDisconnect(OrchestraError):
    """MCP 长连接断开。"""
    is_retryable: ClassVar[bool] = True
    error_code: ClassVar[str] = "MCPDisconnect"


# ---------- 工具：错误名 → 类映射 ----------

_REGISTRY: dict[str, type[OrchestraError]] = {
    cls.error_code: cls
    for cls in (
        AuthError, ToolNotAllowed, InvalidInput, SchemaViolation,
        ApprovalRejected, BudgetExceeded, ConfigurationError,
        TransientError, RateLimited, TimeoutError, MCPDisconnect,
    )
}


def get_error_class(error_code: str) -> type[OrchestraError]:
    """按 error_code 字符串查类（YAML ``nonRetryableErrors`` 引用时用）。"""
    return _REGISTRY.get(error_code, OrchestraError)


def all_non_retryable_codes() -> list[str]:
    """nonRetryable 错误的 error_code 列表（用于默认 retry policy）。"""
    return [code for code, cls in _REGISTRY.items() if not cls.is_retryable]
