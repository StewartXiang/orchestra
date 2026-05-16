# 05 — State 超过 2MB / Event History 接近 50K

**告警**：`StateOversize` (`pipeline_state_size_bytes > 10MB`) / `EventHistoryNearLimit` (`temporal_event_history_size > 40000`)

## 现象
- Workflow Activity 提交 result 时报 `HistoryEvent too large`
- 或 `Workflow exceeds maximum allowed event history size`
- Web UI 看 history 增长异常快

## 处置

### State 大对象
1. `orchestra inspect <id>` 找最大输出 stage
2. 改 stage 配置：
   ```yaml
   output:
     path: "$.code.patch"
     storage: reference   # 改 inline → reference
   ```
   或
   ```yaml
   output:
     path: "$.code.patch"
     storage: oss
     bucket: "orchestra-artifacts"
     ttl: 30d
   ```
3. 重新提交（旧 run 无法救活，记录教训）

### Event History 接近上限
1. 长流水线 / 监听类 Workflow 必须 `workflow.continue_as_new(...)`
2. 在 Workflow 中设阈值：
   ```python
   if completed_stages >= 100:
       workflow.continue_as_new(args=[carry_state])
   ```
3. carry_state 必须 < 2MB

## 预防
- Schema 校验阶段加预估：output 大小估计 > 100KB 强制 reference（warn）
- monitor `pipeline_state_size_bytes` 趋势，做容量规划
- Stage 设计原则：一个 stage 只负责一个语义产出，避免大杂烩
