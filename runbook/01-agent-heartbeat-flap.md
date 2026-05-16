# 01 — Agent 心跳频繁超时

**告警**：`AgentDown` (`agent_heartbeat_lag_seconds > 60`)

## 现象
- Web UI 中 Activity 频繁 `TimedOut(HEARTBEAT)` → 重试
- 流水线 P95 延迟拉长
- `agent_heartbeat_lag_seconds` 高位

## 排查
1. `orchestra agents` 看哪个 profile DEAD/UNHEALTHY
2. `docker compose logs worker-<name> --tail 200` 找堆栈
3. 看进程 CPU / 内存：`docker stats worker-<name>` —— LLM 调用阻塞？
4. 看 LLM 上游：API 是否 429 / 5xx？

## 处置

### 临时
- 重启 worker：`docker compose restart worker-<name>`
- 调高 stage 心跳超时：YAML 中 `timeouts.heartbeat: 60s`（默认 30s）
- 拆分长任务为多 stage（每段 < 10min）

### 长期
- Agent 内部启用 checkpoint 心跳，让重试断点续传
- 评估 LLM 模型切换（成本 / 延迟权衡）
- 加 `livenessProbe.gracePeriod` 容忍突发抖动

## 复盘问句
- 为什么 Agent 卡住没及时心跳？是 LLM 慢还是工具调用阻塞？
- 这个 stage 是不是天然超过 1 个心跳周期？该拆？
