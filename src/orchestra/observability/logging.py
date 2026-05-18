"""结构化 JSON 日志 + 敏感字段 Redact（structlog 可选依赖）。"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"^(secret|token|password|passwd|api[_-]?key|apikey|access_key|private_key)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)


def redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("***" if _SENSITIVE_KEY_RE.match(str(k)) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(item) for item in data]
    if isinstance(data, str):
        return _SENSITIVE_VALUE_RE.sub("***REDACTED***", data)
    return data


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    try:
        import structlog
        logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
        ]
        processors.append(structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer())
        structlog.configure(processors=processors, wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)), context_class=dict, logger_factory=structlog.stdlib.LoggerFactory(), cache_logger_on_first_use=True)
    except ImportError:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> Any:
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)


def bind_pipeline_context(pipeline_id: str, run_id: str | None = None, stage_name: str | None = None, agent_name: str | None = None, trace_id: str | None = None) -> None:
    try:
        import structlog
        ctx: dict[str, str] = {"pipelineId": pipeline_id}
        if run_id: ctx["runId"] = run_id
        if stage_name: ctx["stageName"] = stage_name
        if agent_name: ctx["agentName"] = agent_name
        if trace_id: ctx["traceId"] = trace_id
        structlog.contextvars.bind_contextvars(**ctx)
    except ImportError:
        pass


def clear_pipeline_context() -> None:
    try:
        import structlog
        structlog.contextvars.clear_contextvars()
    except ImportError:
        pass
