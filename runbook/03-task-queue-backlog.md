# 03 — Task Queue 积压

**告警**：`TaskQueueBacklog` (`task_queue_depth > 20` for 3m)

## 现象
- 提交流水线后 stage 长时间 PENDING
- `orchestra status <id>` 看到任务在某 queue 排队
- 上游持续推任务，下游 Worker 处理跟不上

## 排查
1. 看哪个 queue：Grafana System dashboard / `orchestra status <id>`
2. 看 Worker 状态：`orchestra agents` —— Worker 是否还在 polling？
3. `docker compose ps` —— Worker 容器是否健康？
4. Agent 是否 NotReady（READiness probe 失败）？
5. 是否有大量 retry 在原地堆？

## 处置

### 临时
- 扩容 Worker 副本（同 task queue 多进程）：
  ```yaml
  deploy: {replicas: 2}  # docker compose 的 deploy.replicas
  ```
- 临时把 stage 改路由到 standby（`grape`）分担：
  ```yaml
  agentSelector: {role: standby}
  ```
- 暂停低优先级流水线：`orchestra schedule pause <id>`

### 长期
- Stage 粒度太细 → 合并；粒度太粗 → 拆分（heartbeat 利好）
- 启用 capability 路由（同 role 多 profile）
- 评估迁移到 PostgreSQL（SQLite 锁等待）
- 加 Task Queue 级 RPS 限流防雪崩
