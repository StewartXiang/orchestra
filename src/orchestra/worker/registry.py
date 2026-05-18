"""Workflow / Activity / DataConverter 注册。

DataConverter 位于 Client 层（Client.connect 时传入），Worker 自动继承。
"""

from __future__ import annotations

from temporalio.worker import Worker

from ..activities import ALL_ACTIVITIES
from ..workflows import ALL_WORKFLOWS


_PASSTHROUGH_MODULES = [
    "pydantic", "pydantic.plugin", "pydantic.plugin._loader",
    "pydantic._internal", "pydantic.deprecated", "pydantic_core",
    "annotated_types",  # Pydantic 依赖
    "zoneinfo", "sysconfig", "platform", "io", "pathlib",
    "json", "yaml", "datetime", "re", "typing",
    "celpy", "celpy.celtypes",
    "simpleeval",  # expr 表达式求值回退
    "orchestra.state.codec",  # 加密 Codec
    "cryptography", "cryptography.hazmat",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.ciphers.aead",
    "base64",  # codec 依赖
    "opentelemetry", "opentelemetry.propagators", "opentelemetry.propagators.textmap",
    "opentelemetry.context", "opentelemetry.context.context",
    "opentelemetry.environment_variables",
    "opentelemetry.util", "opentelemetry.util._importlib_metadata",
    "orchestra.observability", "orchestra.observability.tracing",
]


def build_worker(
    client: "temporalio.client.Client",
    task_queue: str,
    *,
    max_concurrent_activities: int = 5,
    unsandboxed: bool = False,
) -> Worker:
    """构建 Worker 实例（含所有 Workflow + Activity）。

    DataConverter 由 client 携带（使用 make_client() 创建带正确 converter 的 Client）。
    默认开启 Temporal 沙箱（CLAUDE.md §3 确定性铁律）。
    测试环境需在 build_worker 前预加载 Pydantic 等模块（见 conftest.py _preload）。
    """
    if unsandboxed:
        from temporalio.worker import UnsandboxedWorkflowRunner
        runner = UnsandboxedWorkflowRunner()
    else:
        from temporalio.worker.workflow_sandbox import (
            SandboxRestrictions,
            SandboxedWorkflowRunner,
        )
        restrictions = SandboxRestrictions.default.with_passthrough_modules(
            *_PASSTHROUGH_MODULES
        )
        runner = SandboxedWorkflowRunner(restrictions=restrictions)

    return Worker(
        client,
        task_queue=task_queue,
        workflows=ALL_WORKFLOWS,
        activities=ALL_ACTIVITIES,
        max_concurrent_activities=max_concurrent_activities,
        workflow_runner=runner,
    )


async def make_client(
    host: str = "localhost:7233",
    namespace: str = "default",
    *,
    encryption_key: bytes | None = None,
) -> "temporalio.client.Client":
    """创建带 Pydantic v2 DataConverter + AES-256-GCM 加密的 Temporal Client。

    加密 Codec（EncryptingCodec）自动加密含敏感字段（secret/token/password/apikey）
    的 Payload，Temporal Web UI 中显示密文。只有持有相同密钥的 Worker 可解密。

    :param encryption_key: 32 字节 AES-256 密钥；None 时从环境变量 ORCHESTRA_ENCRYPTION_KEY 读取
    """
    from temporalio.client import Client
    from temporalio.converter import DataConverter

    from ..state.codec import EncryptingCodec

    # 构建 DataConverter（优先 Pydantic，回退默认）
    try:
        from temporalio.contrib.pydantic import pydantic_data_converter  # type: ignore[import-untyped]
        base_converter = pydantic_data_converter
    except ImportError:
        base_converter = DataConverter.default

    # wrap with encryption codec
    codec = EncryptingCodec(key=encryption_key)

    # pydantic_data_converter 是 DataConverter 实例，通过取其类属性重建
    if base_converter is not DataConverter.default:
        from temporalio.contrib.pydantic import PydanticPayloadConverter  # type: ignore[import-untyped]
        converter = DataConverter(
            payload_converter_class=PydanticPayloadConverter,
            payload_codec=codec,
        )
    else:
        converter = DataConverter(payload_codec=codec)

    return await Client.connect(
        host,
        namespace=namespace,
        data_converter=converter,
    )
