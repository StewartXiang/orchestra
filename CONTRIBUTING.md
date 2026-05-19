# Contributing to Agent Orchestra

感谢你的兴趣！本文档帮助你在 Orchestra 上贡献代码、文档或示例流水线。

## 快速上手

```bash
git clone <repo-url>
cd orchestra
pip install -e ".[dev]"
```

## 开发流程

1. **Fork + Branch** — 从 `master` 切出 feature/fix 分支
2. **写代码** — 遵循 [`CLAUDE.md`](CLAUDE.md) 中的约定（导入顺序、类型注解、docstring）
3. **写测试** — 新增代码必须有对应测试（见下方测试策略）
4. **跑检查** — 提交前跑：
   ```bash
   ruff check src tests
   ruff format src tests
   mypy --strict src
   pytest tests/unit tests/replay
   ```
5. **提 PR** — PR 描述必须回答：① 改了什么 ② 为何这样 ③ 如何验证

## 测试策略

| 新增内容 | 必须配套测试 |
|---|---|
| Workflow | `tests/integration/test_workflow_<name>.py` + replay fixture |
| Activity | `tests/unit/` mock 适配器测试（正常 + 取消 + 重试 + 幂等） |
| Schema 字段 | `tests/unit/test_validator.py` 用例（合法 + 非法） |
| Adapter | `tests/unit/test_adapters.py` + MockAdapter |
| CLI 命令 | `tests/unit/test_cli_basic.py` |

```bash
pytest                               # 全量（需要 temporalio）
pytest tests/unit                    # 仅单元
pytest tests/replay                  # Replay 兼容性回归
pytest -m "not integration"          # 跳过集成测试
```

## 项目宪法

提交代码前必须阅读 [`CLAUDE.md`](CLAUDE.md) —— 它定义了：

- **确定性铁律**：Workflow 代码禁止 `time.now()`/`random`/文件IO/网络IO
- **幂等铁律**：Activity 必须 心跳 + 幂等键 + 取消检查
- **代码约定**：import 顺序、单文件 ≤400 行、类型注解强制
- **提交格式**：`<area>: <imperative>`（area ∈ schema/workflow/activity/adapter/...）

## 添加新 Agent

1. 在 `config/capabilities.yaml` 声明 capability（如尚不存在）
2. 在 `config/profiles.yaml` 添加 profile
3. 在 `deploy/docker-compose.yml` 添加 worker 服务

Agent 本身通过 MCP 协议通信——Orchestra 不要求修改 Agent 代码。

## Issue 和讨论

- Bug 报告 → [GitHub Issues](issues)
- 功能建议 → [GitHub Discussions](discussions)（如有）或 Issue + `enhancement` 标签
- 安全问题 → 请私下报告，不要在公开 Issue 中披露

## License

Apache 2.0。提交代码即表示你同意在此许可下分发你的贡献。
