"""Artifact 落盘 Activity。

Stage 执行后由 Workflow 调用，将 Agent 产出物写入 ArtifactStore。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from temporalio import activity

from ..domain.state import ArtifactReference
from ..observability.logging import get_logger
from ..state.artifact_store import get_artifact_store
from ..state.idempotency import get_store

logger = get_logger(__name__)


@dataclass
class ArtifactPutInput:
    source_path: str
    namespace: str
    pipeline_name: str
    run_id: str
    stage_name: str
    artifact_name: str
    compress: bool = False
    retention_days: int | None = None


@activity.defn
async def put_artifact(inp: ArtifactPutInput) -> ArtifactReference:
    """将本地文件/目录写入 ArtifactStore。"""
    activity.heartbeat({"phase": "artifact_started", "name": inp.artifact_name})

    info = activity.info()
    key = f"artifact/{info.workflow_id}/{inp.stage_name}/{inp.artifact_name}"
    store = get_store()

    if cached := await store.get(key):
        logger.info("artifact_cached", name=inp.artifact_name)
        return ArtifactReference(**cached)

    artifact_store = get_artifact_store()
    retention_seconds = inp.retention_days * 86400 if inp.retention_days else None

    ref = artifact_store.put(
        inp.source_path,
        namespace=inp.namespace,
        pipeline_name=inp.pipeline_name,
        run_id=inp.run_id,
        stage_name=inp.stage_name,
        artifact_name=inp.artifact_name,
        compress=inp.compress,
        retention_seconds=retention_seconds,
    )

    activity.heartbeat({"phase": "artifact_done", "sha256": ref.sha256})
    await store.put(key, ref.__dict__, ttl_seconds=86400)

    logger.info("artifact_stored", name=inp.artifact_name, sha256=ref.sha256, size=ref.size)
    return ref
