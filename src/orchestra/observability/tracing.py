"""OpenTelemetry Span 工厂 + 上下文传播（OTel 可选依赖，不可用时 no-op）。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator


def init_tracing(service_name: str = "orchestra-worker", otlp_endpoint: str | None = None) -> None:
    """初始化 OTel TracerProvider。OTel 不可用时静默跳过。"""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # type: ignore[import-untyped]
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)))
        trace.set_tracer_provider(provider)
    except ImportError:
        pass


def inject_context(carrier: dict[str, str]) -> None:
    try:
        from opentelemetry.propagators.textmap import DefaultSetter  # type: ignore[import-untyped]
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        TraceContextTextMapPropagator().inject(carrier)
    except ImportError:
        pass


def extract_context(carrier: dict[str, str]) -> Any:
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        return TraceContextTextMapPropagator().extract(carrier)
    except ImportError:
        return None


def traceparent_from_carrier(carrier: dict[str, str]) -> str | None:
    return carrier.get("traceparent")


def carrier_from_traceparent(traceparent: str | None) -> dict[str, str]:
    return {"traceparent": traceparent} if traceparent else {}


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None, parent_carrier: dict[str, str] | None = None) -> Generator[Any, None, None]:
    """OTel span。OTel 不可用时返回 no-op。"""
    try:
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        ctx = TraceContextTextMapPropagator().extract(parent_carrier) if parent_carrier else None
        with trace.get_tracer("orchestra").start_as_current_span(name, context=ctx) as s:
            if attributes:
                for k, v in attributes.items():
                    s.set_attribute(k, v)
            yield s
    except ImportError:
        yield None  # no-op


def set_pipeline_attributes(s: Any, pipeline_id: str, stage_name: str | None = None) -> None:
    if s is None:
        return
    try:
        s.set_attribute("pipeline.id", pipeline_id)
        if stage_name:
            s.set_attribute("stage.name", stage_name)
    except Exception:
        pass
