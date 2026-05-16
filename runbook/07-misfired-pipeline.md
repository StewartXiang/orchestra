# 07 — 误提交错误流水线

人工触发的事故（提交了错误参数 / 错误版本 / 错误环境）。

## 立即处置

1. **第一反应：能否优雅取消？**
   ```bash
   orchestra cancel <id>
   ```
   走 cleanup（释放 Agent / 清临时文件）

2. **如果已触发副作用**：
   ```bash
   orchestra inspect <id> | grep sideEffects
   ```
   看 sideEffects 字段：`git` / `fs` / `deploy` / `network` / `db`

3. **副作用 = deploy** → 立刻跑回滚流水线：
   ```bash
   orchestra submit examples/rollback.pipeline.yaml --param target_run=<id>
   ```

4. **副作用 = git push** → 主分支 revert，私有分支可保留

5. **强制终止（不走 cleanup）**：
   ```bash
   orchestra cancel <id> --force
   ```
   仅在必须立刻停手时；会留下不一致状态

## 复盘

- 是不是 schema 没检 `target_env` 默认 prod？该把 prod 改到必填且需 approval
- `dryRun: true` 是否该作为 prod 默认？
- 重要流水线加 approval 节点强制人工二次确认
- CLI 提交时缺 `--idempotency-key` 导致重复提交？默认要求？

## 预防
- 高风险 stage 必加 `approval` + `sideEffects` 声明
- `submit` 默认 dryRun，`--apply` 才真执行（待考虑）
