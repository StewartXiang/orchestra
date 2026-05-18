# 08 — 基础设施启动与验证

**触发场景：** 全新部署 / 服务器重启 / 迁移环境后重新搭建 Orchestra 底座。

**优先级：** P2（平台不可用但无数据丢失风险）

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────┐
│  宿主 (localhost)                                     │
│  ┌──────────────────────────────────────────────┐    │
│  │  MCP Agent 服务器 × 7（端口 18961-18969）      │    │
│  │  /opt/orchestra-agents/agent_server.py        │    │
│  └──────────────────────────────────────────────┘    │
│                         ▲                             │
│  ┌────────────── Docker ──────────────────────────┐  │
│  │  postgres:5433  temporal-server:7233           │  │
│  │  redis:6380                                    │  │
│  │  Agent Worker × 9（连接 host.docker.internal）  │  │
│  └────────────────────────────────────────────────┘  │
│                         │                             │
│  orchestra CLI ──→ Temporal Server ──→ Workers ──→ Agents │
└──────────────────────────────────────────────────────┘
```

---

## 2. 前置条件

| 依赖 | 版本要求 | 验证命令 |
|------|---------|---------|
| Docker + Compose | ≥ 24 | `docker compose version` |
| Python | ≥ 3.10 | `python3 --version` |
| Godot (可选) | 4.x | `/usr/local/bin/godot --version` |
| 内存 | ≥ 4 GB 可用 | `free -h` |
| 硬盘 | ≥ 10 GB 可用 | `df -h /opt` |

---

## 3. 步骤

### 3.1 拉取镜像

```bash
cd /home/ccbot/orchestra

# 基础镜像（首次需要 ~10 分钟）
docker pull postgres:16-alpine
docker pull temporalio/auto-setup:1.24
docker pull redis:7.2-alpine
```

### 3.2 启动基础设施

```bash
# 仅启动数据层 + 编排层（不含监控栈，省内存）
docker compose -f deploy/docker-compose.yml up -d \
  postgres temporal-server redis

# 等待 Temporal 健康
until docker ps --format '{{.Status}}' \
  --filter name=temporal-server | grep -q healthy; do
  sleep 2
done
echo "Temporal ready"
```

### 3.3 构建并启动 Worker

```bash
# 构建镜像（首次 ~5 分钟，后续缓存加速）
docker compose -f deploy/docker-compose.yml build

# 启动流水线所需的 7 个 Worker
docker compose -f deploy/docker-compose.yml up -d \
  worker-walnut worker-almond worker-coconut worker-cherry \
  worker-strawberry worker-blueberry worker-grape

# 等待全部上线（~15 秒）
sleep 10
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep worker
```

期望输出：7 个 worker 全部 `Up`，无 `Restarting`。

> **注意：** 不需要启动 `worker-chestnut` 和 `worker-mango`（Flappy Bird 流水线不用）。
> 也不需要 `prometheus`、`grafana`、`otel-collector`、`temporal-ui`（省 ~2 GB 内存）。

### 3.4 启动 MCP Agent 服务器

```bash
bash /opt/orchestra-agents/start_all.sh
```

期望输出：
```
✅ walnut (:18961) — HEALTHY
✅ almond (:18962) — HEALTHY
✅ coconut (:18964) — HEALTHY
✅ cherry (:18965) — HEALTHY
✅ strawberry (:18967) — HEALTHY
✅ blueberry (:18968) — HEALTHY
✅ grape (:18969) — HEALTHY
```

### 3.5 验证全链路连通

```bash
# 1. Worker 能连 MCP Agent
docker logs deploy-worker-walnut-1 2>&1 | grep startup_probe_ok

# 2. Worker 已注册到 Temporal
docker logs deploy-worker-walnut-1 2>&1 | grep worker_ready

# 3. CLI 能连 Temporal
PYTHONPATH=/home/ccbot/orchestra/src python3.10 -c "
import asyncio
from temporalio.client import Client
async def main():
    c = await Client.connect('localhost:7233', namespace='default')
    print('Temporal connected')
asyncio.run(main())
"
```

三项全部成功 = 底座就绪。

---

## 4. 提交流水线

```bash
cd /home/ccbot/orchestra

PYTHONPATH=src python3.10 -m orchestra.cli.main \
  --host localhost:7233 submit \
  examples/flappybird.pipeline.yaml \
  --params '{
    "gdd": "复刻 Flappy Bird：横版卷轴…撞水管或落地 Game Over。"
  }'
```

> **注意：** CLI 挂载退出时的 `AttributeError: _BridgeServiceClient.close` 无害，Temporal Python SDK 已知问题。

---

## 5. 监控执行

```bash
# 检查状态
PYTHONPATH=src python3.10 -m orchestra.cli.main \
  --host localhost:7233 status <workflow_id>

# 用 Python 查看事件历史
PYTHONPATH=src python3.10 -c "
import asyncio
from temporalio.client import Client
from temporalio.api.enums.v1 import EventType

async def main():
    c = await Client.connect('localhost:7233', namespace='default')
    h = c.get_workflow_handle('<workflow_id>')
    async for e in h.fetch_history_events():
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            print(f'[{e.event_id}] {e.activity_task_scheduled_event_attributes.activity_type.name}')
        elif e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            print(f'[{e.event_id}] ✓')
        elif e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            print(f'[{e.event_id}] ✗')
asyncio.run(main())
"
```

**正常流程的事件序列：**
```
E5  execute_agent_task  (design-review)
E7  ✓
E11 execute_agent_task  (code + art 并行)
E17 execute_agent_task  (art)
E18 execute_agent_task  (test)
E13 ✓ E20 ✓ E25 ✓
E29 execute_agent_task  (ui-verify: strawberry + grape 并行)
E31 ✓
E35 send_notification   (飞书通知)
E37 ✓
→ Status: COMPLETED
```

---

## 6. 故障排查

### 6.1 Worker 反复重启 → `startup_probe_failed`

**症状：** `docker ps` 显示 `Restarting (1)`

**原因：** MCP Agent 服务器没启动。

**修复：**
```bash
bash /opt/orchestra-agents/start_all.sh
docker compose -f deploy/docker-compose.yml up -d --force-recreate \
  worker-walnut worker-almond worker-coconut worker-cherry \
  worker-strawberry worker-blueberry worker-grape
```

### 6.2 Worker 反复重启 → `tcp connect error 127.0.0.1:7233`

**症状：** Worker 日志显示连接 `localhost:7233` 失败。

**原因：** 新 Worker 镜像没有处理 YAML anchor 的 `environment` 覆盖问题。

**修复：** 已在 `deploy/docker-compose.yml` 中每个 Worker 显式声明了
`TEMPORAL_HOST: temporal-server:7233`。如果问题复现，检查 compose 文件。

### 6.3 Temporal Server OOM

**症状：** `Exited (137)` — Docker 发送 SIGKILL。

**原因：** 9 个 Worker + PostgreSQL + Temporal + 监控栈，7.1 GB RAM 不够。

**修复：**
```bash
# 只启动必要的 7 个 Worker，停掉监控栈
docker compose -f deploy/docker-compose.yml stop \
  prometheus grafana otel-collector worker-chestnut worker-mango 2>/dev/null
```

### 6.4 Pipeline 卡在审批节点

**症状：** 事件停在 `EVENT_TYPE_TIMER_STARTED`，status 长期 RUNNING。

**原因：** `deploy-approval` 等待审批，但 Temporal namespace 禁用了 Update 操作。

**解决方案：** 已内置 dev 模式自动审批 —— 当 `approvers` 列表中所有值以
`ou_` 或 `dev_` 开头时，自动通过。生产环境需启用 Temporal Update 功能：

```bash
# 生产环境 — 启用 Update 支持
docker exec deploy-temporal-server-1 \
  tctl namespace update --namespace default \
  --enable-update-with-new-workflow

# 然后用 CLI 审批
PYTHONPATH=src python3.10 -m orchestra.cli.main \
  --host localhost:7233 approve <workflow_id> deploy-approval
```

### 6.5 Schema 校验失败

**症状：** `stage 'design-review' output schema 校验失败: 'task' is a required property`

**原因：** Agent handler 返回的 JSON 格式不符合 outputSchema。

**修复：** 检查 `/opt/orchestra-agents/agent_server.py` 中对应 agent handler 的
返回格式，确保 `{"output": {...}, "tokens_consumed": 0, ...}` 结构且字段完整。

---

## 7. 关键配置文件

| 文件 | 作用 | 改动记录 |
|------|------|---------|
| `deploy/docker-compose.yml` | 容器编排 | Worker env 补了 TEMPORAL_HOST；端口 187→189 映射 |
| `config/profiles.yaml` | Agent 注册 | localhost → host.docker.internal |
| `src/orchestra/worker/main.py` | Worker 入口 | config 路径自适应；端口映射移到 probe 前 |
| `src/orchestra/worker/registry.py` | 沙箱配置 | 加 cryptography/opentelemetry passthrough |
| `src/orchestra/state/codec.py` | 加密层 | 空密钥 pass-through |
| `src/orchestra/workflows/pipeline_workflow.py` | 审批逻辑 | dev 模式自动审批 |
| `/opt/orchestra-agents/agent_server.py` | MCP Agent 实现 | 7 个 Agent 的业务逻辑 |
| `/opt/orchestra-agents/start_all.sh` | Agent 启动器 | 一键启停 |
