"""共享测试 Fixtures。

- mock_adapter: MockAgentAdapter（SUCCESS 模式）
- mock_registry: 全量 9 agent mock registry
- temp_db: 临时 SQLite 路径（自动清理）
- pipeline_minimal: parsed minimal Pipeline 对象
- pipeline_game_dev: parsed game-dev Pipeline 对象
- state_store: 已初始化的 StateStore
- idempotency_store: 临时 SQLiteIdempotencyStore
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orchestra.adapters.mock import MockAgentAdapter, MockBehavior
from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
from orchestra.domain.enums import Role
from orchestra.schema.parser import parse_pipeline
from orchestra.state.idempotency import SQLiteIdempotencyStore
from orchestra.state.store import StateStore


# ──────────────────────────────────────────────
# 全局状态重置（防止测试间污染）
# ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_global_state():
    """每个测试结束后将全局 singleton 恢复到安全状态。

    orchestra.adapters.registry._REGISTRY 和 orchestra.state.idempotency._store
    是模块级全局变量，可能在测试之间产生干扰。
    """
    yield
    # 测试后清理
    try:
        import orchestra.adapters.registry as _reg
        _reg._REGISTRY.clear()
    except Exception:
        pass
    # idempotency store 不重置（每个测试通过 init_store 自行覆盖）


# ──────────────────────────────────────────────
# 基础 Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def profiles(project_root):
    return load_profiles_from_yaml(project_root / "config" / "profiles.yaml")


@pytest.fixture
def mock_adapter():
    return MockAgentAdapter(
        profile_name="walnut",
        role=Role.DEVELOPER,
        capabilities=["python", "godot"],
        tools=["file_read", "file_write"],
        behavior=MockBehavior.SUCCESS,
        output={"patch": "test diff content", "files_changed": ["main.py"]},
    )


@pytest.fixture
def mock_adapter_fail():
    return MockAgentAdapter(
        profile_name="walnut",
        behavior=MockBehavior.FAIL,
        error_message="LLM timeout",
        error_code="TimeoutError",
    )


@pytest.fixture
def mock_registry(profiles):
    return build_registry(
        profiles,
        use_mock=True,
        mock_outputs={
            "walnut": {"patch": "walnut output"},
            "almond": {"result": "pass", "coverage": 92.5},
            "chestnut": {"patch": "chestnut output"},
            "coconut": {"deployed": True, "url": "https://example.com"},
            "cherry": {"assets": ["ui.png"]},
            "mango": {"shader": "glsl code"},
            "strawberry": {"ui_result": "pass"},
            "blueberry": {"summary": "looks good"},
            "grape": {"result": "ok"},
        },
    )


# ──────────────────────────────────────────────
# Pipeline Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def pipeline_minimal(project_root):
    return parse_pipeline(project_root / "examples" / "minimal.pipeline.yaml")


@pytest.fixture(scope="session")
def pipeline_game_dev(project_root):
    return parse_pipeline(project_root / "examples" / "game-dev.pipeline.yaml")


@pytest.fixture(scope="session")
def pipeline_parameterized(project_root):
    return parse_pipeline(project_root / "examples" / "parameterized.pipeline.yaml")


# ──────────────────────────────────────────────
# State / Storage Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def state_store():
    return StateStore(initial_params={"task": "implement feature X"})


@pytest.fixture
def state_store_with_outputs():
    store = StateStore(initial_params={"task": "implement"})
    store.register_stage_output("code", "$.code.patch")
    store.register_stage_output("test", "$.test.result")
    return store


@pytest_asyncio.fixture
async def idempotency_store(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "idempotency.db")
    yield store
    await store.close()


@pytest.fixture
def artifact_store(tmp_path):
    from orchestra.state.artifact_store import LocalArtifactStore
    return LocalArtifactStore(tmp_path / "artifacts")


# ──────────────────────────────────────────────
# Temporal TestWorkflowEnvironment（集成测试）
# ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def temporal_env():
    """Temporal in-process 测试环境（无需真实 Server）。"""
    try:
        from temporalio.testing import WorkflowEnvironment
        # 使用 pydantic_data_converter 消除 Pydantic v2 deprecation warning
        try:
            from temporalio.contrib.pydantic import pydantic_data_converter  # type: ignore[import-untyped]
            async with await WorkflowEnvironment.start_time_skipping(
                data_converter=pydantic_data_converter
            ) as env:
                yield env
        except (ImportError, TypeError):
            # 旧版 SDK 不支持 data_converter 参数
            async with await WorkflowEnvironment.start_time_skipping() as env:
                yield env
    except ImportError:
        pytest.skip("temporalio not installed")


@pytest_asyncio.fixture
async def temporal_worker(temporal_env, mock_registry):
    """在 TestWorkflowEnvironment 中运行的 Worker（mock Adapter）。"""
    try:
        from orchestra.worker.registry import build_worker
        from orchestra.state.idempotency import init_store
        from orchestra.state.artifact_store import init_artifact_store
        from orchestra.observability.audit import init_audit_writer

        import tempfile
        tmp = tempfile.mkdtemp()
        init_store("memory")  # 测试用内存存储，避免 SQLite 并发问题
        init_artifact_store(os.path.join(tmp, "artifacts"))
        init_audit_writer(os.path.join(tmp, "audits.db"))

        async with build_worker(temporal_env.client, "test-queue") as worker:
            yield worker
    except ImportError:
        pytest.skip("temporalio not installed")
