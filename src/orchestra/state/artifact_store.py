"""Artifact 落盘（local / s3 / oss 后端）+ 校验和 + 清理策略。

Stage 产出物通过 ArtifactStore 统一管理：
  - 写：stage 执行后由 Activity 调用 put() 落盘
  - 读：下游 Stage 通过 inputArtifacts 引用，由 Activity 调用 get() 获取
  - 清理：retention 到期后由后台任务调用 cleanup()

目录结构（来自 design.md）：
  {base_path}/{namespace}/{pipeline_name}/{run_id}/{stage}/{artifact}/
    ├── data.tar.gz  （或直接文件）
    └── manifest.json ← {hash, size, createdAt, retention}
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.state import ArtifactReference


@dataclass
class ArtifactManifest:
    name: str
    path: str
    sha256: str
    size: int
    created_at: float
    retention_seconds: int | None
    compressed: bool
    storage: str = "local"


class LocalArtifactStore:
    """本地文件系统 Artifact 存储。"""

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def artifact_dir(
        self,
        namespace: str,
        pipeline_name: str,
        run_id: str,
        stage_name: str,
        artifact_name: str,
    ) -> Path:
        return self._base / namespace / pipeline_name / run_id / stage_name / artifact_name

    def put(
        self,
        source_path: str | Path,
        *,
        namespace: str,
        pipeline_name: str,
        run_id: str,
        stage_name: str,
        artifact_name: str,
        compress: bool = False,
        retention_seconds: int | None = None,
    ) -> ArtifactReference:
        """将 source_path 复制到 Artifact 存储并返回引用。"""
        src = Path(source_path)
        dest_dir = self.artifact_dir(namespace, pipeline_name, run_id, stage_name, artifact_name)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if compress and src.is_dir():
            dest_file = dest_dir / "data.tar.gz"
            with tarfile.open(dest_file, "w:gz") as tar:
                tar.add(src, arcname=src.name)
            sha256 = _sha256_file(dest_file)
            size = dest_file.stat().st_size
            final_path = str(dest_file)
        elif compress and src.is_file():
            dest_file = dest_dir / (src.name + ".gz")
            with open(src, "rb") as fi, gzip.open(dest_file, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            sha256 = _sha256_file(dest_file)
            size = dest_file.stat().st_size
            final_path = str(dest_file)
        elif src.is_dir():
            dest_subdir = dest_dir / src.name
            if dest_subdir.exists():
                shutil.rmtree(dest_subdir)
            shutil.copytree(src, dest_subdir)
            sha256, size = _sha256_dir(dest_subdir)
            final_path = str(dest_subdir)
        else:
            dest_file = dest_dir / src.name
            shutil.copy2(src, dest_file)
            sha256 = _sha256_file(dest_file)
            size = dest_file.stat().st_size
            final_path = str(dest_file)

        manifest = ArtifactManifest(
            name=artifact_name,
            path=final_path,
            sha256=sha256,
            size=size,
            created_at=time.time(),
            retention_seconds=retention_seconds,
            compressed=compress,
            storage="local",
        )
        _write_manifest(dest_dir, manifest)

        return ArtifactReference(path=final_path, sha256=sha256, size=size, storage="local")

    def get(self, reference: ArtifactReference) -> Path:
        """返回本地路径（已存在则直接返回）。"""
        path = Path(reference.path)
        if not path.exists():
            raise FileNotFoundError(f"Artifact 不存在: {path}")
        return path

    def cleanup_expired(self) -> int:
        """清理过期 Artifact，返回删除的文件/目录数量。"""
        now = time.time()
        deleted = 0
        for manifest_file in self._base.rglob("manifest.json"):
            try:
                manifest = json.loads(manifest_file.read_text())
                created_at = manifest.get("created_at", 0)
                retention = manifest.get("retention_seconds")
                if retention and (now - created_at) > retention:
                    artifact_dir = manifest_file.parent
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                    deleted += 1
            except Exception:
                pass
        return deleted


class S3ArtifactStore:
    """S3 后端（占位，Phase 3 实现）。"""

    def put(self, *args: Any, **kwargs: Any) -> ArtifactReference:
        raise NotImplementedError("S3ArtifactStore 尚未实现，请使用 LocalArtifactStore")

    def get(self, reference: ArtifactReference) -> Any:
        raise NotImplementedError


# ---------- 模块级实例 ----------

_store: LocalArtifactStore | None = None


def init_artifact_store(base_path: str | Path) -> LocalArtifactStore:
    global _store
    _store = LocalArtifactStore(base_path)
    return _store


def get_artifact_store() -> LocalArtifactStore:
    if _store is None:
        raise RuntimeError("ArtifactStore 未初始化，请先调用 init_artifact_store()")
    return _store


# ---------- 工具函数 ----------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    total_size = 0
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            rel = str(file_path.relative_to(path))
            h.update(rel.encode())
            file_hash = _sha256_file(file_path)
            h.update(file_hash.encode())
            total_size += file_path.stat().st_size
    return h.hexdigest(), total_size


def _write_manifest(dest_dir: Path, manifest: ArtifactManifest) -> None:
    (dest_dir / "manifest.json").write_text(
        json.dumps({
            "name": manifest.name,
            "path": manifest.path,
            "sha256": manifest.sha256,
            "size": manifest.size,
            "created_at": manifest.created_at,
            "retention_seconds": manifest.retention_seconds,
            "compressed": manifest.compressed,
            "storage": manifest.storage,
        }, indent=2)
    )
