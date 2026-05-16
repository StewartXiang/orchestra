# state/

## 职责
Workflow 全局 State 与外部存储之间的唯一桥梁。读写 State 路径、产物落盘、Payload 加密、幂等键。

## 关键文件

| 文件 | 责任 |
|---|---|
| `store.py` | State 读写 + 写隔离校验（Stage 仅能写自己的 output 路径） |
| `artifact_store.py` | local / s3 / oss 后端，统一 `put / get / hash / cleanup` |
| `codec.py` | Temporal Custom PayloadCodec：含密钥的字段 AES-256-GCM 加密；redact 规则 |
| `idempotency.py` | 幂等键存储（Redis 优先，SQLite fallback），TTL 24h |

## 大对象策略

`output.storage`：
- `inline`：直接进 State（< 100KB）
- `reference`：State 存 `{path, sha256, size}`，本体落 `artifacts/`
- `oss`：上传到对象存储，State 存 OSS URL + meta

写入预检：序列化测大小，> 2MB 强制改 reference + warning。

## 写隔离

Stage `output.path = "$.code.patch"` 只允许写到 `state["code"]["patch"]`。下游 Stage 通过 `$.code.patch` 读取。violation → 抛 `SchemaViolation`。

## 边界
- 仅 `activities/` 调用本目录；workflows/ 不能直接 import
- codec 注册到 Temporal Client 后才生效，注册位置在 `worker/main.py`

## 测试策略
- `tests/unit/test_state.py`：写隔离 + 大小阈值切换 + 路径合并
- `tests/unit/test_codec.py`：encrypt/decrypt 往返 + redact

## 常见陷阱
- 用 `{}.update()` 合并 State → 深嵌套覆盖丢失；用 `deep_merge`
- 加密只覆盖 input 不覆盖 output → 敏感字段从输出泄露
- 幂等 store 用 in-memory dict → Worker 重启丢失，必须 Redis/SQLite
