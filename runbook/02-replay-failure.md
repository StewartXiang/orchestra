# 02 — Workflow Replay 失败

**告警**：`WorkflowReplayFailure` (`pipeline_replay_failure_total` 增长)
**严重度**：P1（确定性破坏，运行中的 Workflow 升级后会崩）

## 现象
- CI 中 `pytest tests/replay` 失败
- 错误：`NonDeterministicWorkflowError: ... mismatch ...`
- 报错指出具体 Event 类型（ActivityTaskScheduled / TimerStarted / ChildWorkflowExecutionStarted 等）

## 处置（标准流程）

1. 跑 `pytest tests/replay -v` 取失败堆栈，记下 Event 类型 + Workflow 名
2. `git log --oneline -- src/orchestra/workflows/<file>` 找最近 commit
3. `git diff <prev>..HEAD -- src/orchestra/workflows/<file>` 看改动
4. 用 `replay-guardian` 子 agent 给出 `workflow.get_version` 补丁建议
5. 修改代码：
   ```python
   v = workflow.get_version("<change-name>", workflow.DEFAULT_VERSION, 1)
   if v == workflow.DEFAULT_VERSION:
       # 旧路径
   else:
       # 新路径
   ```
6. 重跑 `pytest tests/replay` 直到全绿
7. 提 PR 并附 `# REPLAY-COMPAT: get_version("<change-name>") added in <PR-link>`

## 不要做
- ❌ 直接删除失败的 fixture（掩盖问题）
- ❌ 改 fixture 让它通过（破坏审计）
- ❌ rebase 强推 commit（多人协作时丢历史）

## 预防
- 修 Workflow 前先看 `replay-guardian` 子 agent 的反模式清单
- CI 之外：本地 `uv run pytest tests/replay` 是 commit 前的最后一道关
