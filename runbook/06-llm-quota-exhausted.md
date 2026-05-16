# 06 — LLM 配额耗尽 / 429

**告警**：`LLMCostBudgetExceeded`（小时级累计成本超阈值）/ Activity 抛 `BudgetExceeded` / `429 Too Many Requests`

## 现象
- 大量 Activity 失败 reason=`BudgetExceeded`
- LLM upstream 返回 429
- `llm_tokens_consumed_total` 速率异常高

## 排查
1. Grafana Agent dashboard → LLM token / 分钟趋势
2. 看哪个 agent / pipeline 是大头
3. 是否有循环回放（loop stage 没 maxIterations）？
4. 是否 cache 失效（不同 input 哈希但语义相同）？

## 处置

### 临时
- 提高 retry 间隔：`retry.maxInterval: 10m`，`coefficient: 3`
- 切换备用模型：用 `aggregateStrategy: any` 多模型兜底
- 暂停低优先级流水线：`orchestra schedule pause <id>`

### 长期
- 启用 stage `cache`（`cache.enabled: true`），相同输入复用结果
- 调小 agent `resources.limits.tokensPerMinute`，做令牌桶限流防过载
- 模型路由：低复杂任务用更便宜的模型
- 复盘：是不是 prompt 设计太啰嗦？
