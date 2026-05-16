"""単元测试：State 层 — StateStore + ArtifactStore + idempotency。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orchestra.domain.errors import SchemaViolation
from orchestra.domain.state import ArtifactReference, StageOutput
from orchestra.state.idempotency import SQLiteIdempotencyStore
from orchestra.state.store import StateStore


# ── StateStore ───────────────────────────────

def test_state_merge_output():
    store = StateStore(initial_params={"task": "hello"})
    store.register_stage_output("code", "$.code.patch")
    out = StageOutput(
        stage_name="code",
        success=True,
        output_path="$.code.patch",
        output_value="diff content",
        started_at_iso="2026-01-01T00:00:00Z",
        completed_at_iso="2026-01-01T00:05:00Z",
    )
    store.merge_stage_output(out)
    assert store.get("$.code.patch") == "diff content"


def test_state_params_readable():
    store = StateStore(initial_params={"env": "prod"})
    assert store.get("$.params.env") == "prod"


def test_state_write_isolation_violation():
    """Stage 不能写其他 Stage 的输出路径。"""
    store = StateStore()
    store.register_stage_output("code", "$.code.patch")
    store.register_stage_output("test", "$.code.patch")  # 冲突

    out = StageOutput(
        stage_name="test",
        success=True,
        output_path="$.code.patch",  # 试图写 code 的路径
        output_value="hijack",
        started_at_iso="2026-01-01T00:00:00Z",
        completed_at_iso="2026-01-01T00:01:00Z",
    )
    with pytest.raises(SchemaViolation):
        store.merge_stage_output(out)


def test_state_snapshot_roundtrip():
    store = StateStore(initial_params={"x": 1})
    store.register_stage_output("code", "$.code.result")
    out = StageOutput(
        stage_name="code", success=True, output_path="$.code.result", output_value=42,
        started_at_iso="2026-01-01T00:00:00Z", completed_at_iso="2026-01-01T00:01:00Z",
    )
    store.merge_stage_output(out)
    snap = store.snapshot()
    restored = StateStore.from_snapshot(snap, {"code": "$.code.result"})
    assert restored.get("$.code.result") == 42
    assert restored.get("$.params.x") == 1


def test_state_size():
    store = StateStore(initial_params={"data": "x" * 1000})
    assert store.size_bytes() > 1000


# ── Idempotency ─────────────────────────────

@pytest.mark.asyncio
async def test_sqlite_get_put(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "idem.db")
    await store.put("k1", {"v": 1})
    result = await store.get("k1")
    assert result == {"v": 1}
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_miss(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "idem.db")
    result = await store.get("missing")
    assert result is None
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_ttl_expired(tmp_path):
    import time
    store = SQLiteIdempotencyStore(tmp_path / "idem.db")
    # TTL = 0 → 立即过期
    await store.put("k-expire", {"v": "old"}, ttl_seconds=0)
    # 稍等确保 expires_at < now
    await asyncio.sleep(0.01)
    result = await store.get("k-expire")
    assert result is None
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_overwrite(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "idem.db")
    await store.put("k2", {"v": 1})
    await store.put("k2", {"v": 2})  # OR REPLACE
    result = await store.get("k2")
    assert result == {"v": 2}
    await store.close()


# ── ArtifactStore ────────────────────────────

def test_artifact_put_file(tmp_path):
    from orchestra.state.artifact_store import LocalArtifactStore

    src = tmp_path / "output.txt"
    src.write_text("hello artifact")
    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = store.put(
        src,
        namespace="default",
        pipeline_name="test",
        run_id="run-1",
        stage_name="code",
        artifact_name="output",
    )
    assert ref.sha256
    assert ref.size > 0
    retrieved = store.get(ref)
    assert retrieved.exists()
    assert retrieved.read_text() == "hello artifact"


def test_artifact_put_dir(tmp_path):
    from orchestra.state.artifact_store import LocalArtifactStore

    src = tmp_path / "build"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")

    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = store.put(
        src,
        namespace="default",
        pipeline_name="test",
        run_id="run-1",
        stage_name="build",
        artifact_name="build_dir",
    )
    assert ref.sha256
    assert ref.size > 0
