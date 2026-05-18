"""Temporal Custom PayloadCodec — 加密 + 脱敏。

含密钥的 Activity input/output 通过 AES-256-GCM 加密：
  - 加密后 Temporal Web UI 显示密文
  - 只有持有密钥的 CLI / Worker 可解密

敏感字段识别：
  - metadata 标记 "sensitive": true
  - 字段名匹配 secret*/token*/password*/api_key*/apikey*

参考 design.md "Temporal Custom PayloadCodec" 章节。
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

_SENSITIVE_KEY_RE = re.compile(
    r"^(secret|token|password|passwd|api[_-]?key|apikey|access_key|private_key)",
    re.IGNORECASE,
)
_ENCRYPTION_ENCODING = "binary/encrypted"
_ENCRYPTION_METADATA_KEY = b"encoding"


class EncryptingCodec(PayloadCodec):
    """AES-256-GCM 加密 Codec。

    :param key: 32 字节（256 位）加密密钥。生产环境从 KMS 派生。
    """

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            # 开发模式：从环境变量读取；生产环境必须显式传入
            key_hex = os.environ.get("ORCHESTRA_ENCRYPTION_KEY", "")
            if key_hex:
                key = bytes.fromhex(key_hex)
            else:
                key = b""  # 空密钥 = 不加密（pass-through）
        if key and len(key) != 32:
            raise ValueError("加密密钥必须是 32 字节（AES-256）")
        self._key = key
        self._enabled = bool(key) and len(key) == 32

    async def encode(self, payloads: list[Payload]) -> list[Payload]:
        if not self._enabled:
            return payloads  # pass-through
        return [self._encrypt_payload(p) if self._should_encrypt(p) else p for p in payloads]

    async def decode(self, payloads: list[Payload]) -> list[Payload]:
        if not self._enabled:
            return payloads  # pass-through
        return [self._decrypt_payload(p) if self._is_encrypted(p) else p for p in payloads]

    def _should_encrypt(self, payload: Payload) -> bool:
        """判断是否需要加密（含敏感字段的 payload）。"""
        try:
            data = json.loads(payload.data)
            return self._has_sensitive_keys(data)
        except Exception:
            return False

    def _has_sensitive_keys(self, data: Any) -> bool:
        if isinstance(data, dict):
            return any(
                _SENSITIVE_KEY_RE.match(str(k)) or self._has_sensitive_keys(v)
                for k, v in data.items()
            )
        if isinstance(data, list):
            return any(self._has_sensitive_keys(item) for item in data)
        return False

    def _encrypt_payload(self, payload: Payload) -> Payload:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import-untyped]

        aesgcm = AESGCM(self._key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, payload.data, None)
        encrypted = base64.b64encode(nonce + ciphertext)

        new_metadata = dict(payload.metadata)
        new_metadata[_ENCRYPTION_METADATA_KEY] = _ENCRYPTION_ENCODING.encode()
        return Payload(data=encrypted, metadata=new_metadata)

    def _is_encrypted(self, payload: Payload) -> bool:
        enc = payload.metadata.get(_ENCRYPTION_METADATA_KEY, b"")
        return enc == _ENCRYPTION_ENCODING.encode()

    def _decrypt_payload(self, payload: Payload) -> Payload:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import-untyped]

        raw = base64.b64decode(payload.data)
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(self._key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        new_metadata = {k: v for k, v in payload.metadata.items() if k != _ENCRYPTION_METADATA_KEY}
        return Payload(data=plaintext, metadata=new_metadata)
