"""幂等键存储（Redis 优先，SQLite 回退）。

TTL = 24h（设计文档约定）。
key 结构：{namespace}:{workflowId}:{activityId}
  - 不含 attempt，重试同一 activityId 命中缓存直接返回

接口统一，后端可切换：
  - Redis：asyncio-native，推荐生产
  - SQLite：aiosqlite，单机无额外服务依赖
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# ---------- 抽象接口 ----------

class IdempotencyStore:
    """幂等键存储抽象基类。"""

    async def get(self, key: str) -> Any | None:
        raise NotImplementedError

    async def put(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        pass


# ---------- Redis 后端 ----------

class RedisIdempotencyStore(IdempotencyStore):
    """基于 Redis 的幂等键存储。

    依赖 redis-py asyncio 接口（redis>=5.0）。
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url = redis_url
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
            self._redis = await aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    async def get(self, key: str) -> Any | None:
        r = await self._get_redis()
        raw = await r.get(f"idempotency:{key}")
        if raw is None:
            return None
        return json.loads(raw)

    async def put(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        r = await self._get_redis()
        await r.setex(f"idempotency:{key}", ttl_seconds, json.dumps(value))

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()


# ---------- Memory 后端（测试 / 高并发场景）----------

class MemoryIdempotencyStore(IdempotencyStore):
    """纯内存幂等键存储。无持久化，进程重启丢失。
    适用：单元测试、负载测试（避免 SQLite 写争用）。
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at > 0 and time.time() > expires_at:
            del self._data[key]
            return None
        return value

    async def put(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0
        self._data[key] = (value, expires_at)

    async def close(self) -> None:
        self._data.clear()


# ---------- SQLite 后端 ----------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expires ON idempotency(expires_at);
"""

_SQLITE_GET = "SELECT value FROM idempotency WHERE key = ? AND expires_at > ?"
_SQLITE_PUT = "INSERT OR REPLACE INTO idempotency (key, value, expires_at) VALUES (?, ?, ?)"
_SQLITE_CLEANUP = "DELETE FROM idempotency WHERE expires_at <= ?"


class SQLiteIdempotencyStore(IdempotencyStore):
    """基于 SQLite 的幂等键存储（aiosqlite 异步接口）。"""

    def __init__(self, db_path: str | Path = "idempotency.db") -> None:
        self._db_path = str(db_path)
        self._db: Any = None

    def _get_sync_conn(self) -> "sqlite3.Connection":
        import sqlite3 as _sqlite3
        if self._db is None:
            self._db = _sqlite3.connect(self._db_path, check_same_thread=False)
            self._db.executescript(_SQLITE_SCHEMA)
            self._db.commit()
        return self._db  # type: ignore[return-value]

    async def get(self, key: str) -> Any | None:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get, key)

    def _sync_get(self, key: str) -> Any | None:
        conn = self._get_sync_conn()
        now = time.time()
        row = conn.execute(_SQLITE_GET, (key, now)).fetchone()
        return json.loads(row[0]) if row else None

    async def put(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        import asyncio
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_put, key, value, ttl_seconds)

    def _sync_put(self, key: str, value: Any, ttl_seconds: int) -> None:
        conn = self._get_sync_conn()
        expires_at = time.time() + ttl_seconds
        conn.execute(_SQLITE_PUT, (key, json.dumps(value), expires_at))
        conn.commit()

    async def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None


# ---------- 模块级实例 ----------

_store: IdempotencyStore | None = None


def init_store(backend: str = "sqlite", **kwargs: Any) -> IdempotencyStore:
    """初始化模块级幂等键存储。Worker 启动时调用一次。

    :param backend: "redis" 或 "sqlite"
    :param kwargs: 传给对应后端的构造参数
    """
    global _store
    if backend == "redis":
        _store = RedisIdempotencyStore(**kwargs)
    elif backend == "memory":
        _store = MemoryIdempotencyStore()
    else:
        _store = SQLiteIdempotencyStore(**kwargs)
    return _store


def get_store() -> IdempotencyStore:
    if _store is None:
        raise RuntimeError("IdempotencyStore 未初始化，请先调用 init_store()")
    return _store
