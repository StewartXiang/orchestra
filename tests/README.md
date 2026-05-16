# tests/

测试金字塔：

```
       ┌──────────┐
       │ Chaos    │  故障注入（kill agent / network / state corrupt）
       ├──────────┤
       │ Load     │  吞吐 / 延迟基线
       ├──────────┤
       │ E2E      │  完整流水线（mock Agent）— integration/
       ├──────────┤
       │ Replay   │  Temporal Replay 兼容性回放（CI 强制）
       ├──────────┤
       │ Integ    │  Schema + DAG + Adapter 通信
       ├──────────┤
       │ Unit     │  表达式 / 拓扑 / 超时 / Codec
       └──────────┘
```

## 各层验收标准

| 层 | 标记 | 必须 | 频率 |
|---|---|---|---|
| `unit/` | `@pytest.mark.unit` | 纯 Python，无 IO，毫秒级 | 每次 commit |
| `integration/` | `@pytest.mark.integration` | 起 Temporal dev server + mock Adapter | 每次 PR |
| `replay/` | `@pytest.mark.replay` | 加载 fixture history + WorkflowReplayer | 每次 PR（CI 强制） |
| `chaos/` | `@pytest.mark.chaos` | 启 docker-compose 全套 + 注入故障 | 夜跑 |
| `load/` | `@pytest.mark.load` | 1000 stage 并发 mock，60s 内完成 | 发布前 |

## 命名约定
- `test_<module>_<scenario>.py`
- 一个测试函数只断一件事
- fixtures 走 `tests/conftest.py` 共享

## CI 强制
- `pytest tests/unit -v --cov=src/orchestra --cov-fail-under=80`
- `pytest tests/replay`
- `ruff check src tests`
- `mypy --strict src`

## 测试不通过 = 不合并
- Replay 失败 → 阻止合并，要求 `workflow.get_version` 兼容补丁
- Coverage 退化 > 5% → 警告
- Load 关键指标退化 > 20% → 阻止发布
