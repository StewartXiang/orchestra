# 04 — 审批长时间未响应

**告警**：`ApprovalPending` (`approval_pending_total > 0` and `now - approval_started_at > 30m`)

## 现象
- 流水线卡在某 `approval` stage
- 审批人未点击通知
- 流水线即将触发 `onTimeout` 动作

## 处置

1. `orchestra status <id> --pending-approvals` 看待审批 stage 详情
2. 飞书 / 邮件通知是否送达？检查 `notification.channels` 配置
3. 审批人是否在岗 / 误删消息？
4. 紧急时手动审批：
   ```bash
   orchestra approve <id> <stage> --as <user>
   # 或
   orchestra reject  <id> <stage> --reason "..." --as <user>
   ```

## 调整
- 缩短 `approval.timeout`（如 1h → 30min）
- 启用 `escalateTo` 升级人
- 多人审批：`policy: any`（一个签就行）
- 加 `reminderInterval`（10min 复发提醒）

## 长期
- 高频审批节点考虑改为自动化判断（`condition` + 规则）
- 审批超时默认值是否合理？dev 环境短，prod 环境长
