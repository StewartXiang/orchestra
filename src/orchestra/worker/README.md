# worker/

## 职责
Worker 进程入口。注册 Workflow / Activity，连接 Temporal，启动心跳服务、健康检查端点、Prometheus 暴露端口、OTel exporter。

## 关键文件

| 文件 | 责任 |
|---|---|
| `main.py` | `python -m orchestra.worker.main` 入口；连 Temporal、加载 profile、启动 Worker |
| `registry.py` | 注册 Workflow / Activity / Adapter / PayloadCodec |
| `lifecycle.py` | startup probe（自检 MCP/LLM 可达）+ 优雅关闭（SIGTERM 处理 + drain） |

## 启动流程
1. 读 `PROFILE_NAME` 环境变量 → 加载 `config/profiles.yaml` 中对应 profile
2. 跑 startup probe（MCP 可达、模型 API 可调） → 失败则不注册到 Task Queue
3. 连接 Temporal Server，注册 Codec / Workflow / Activity
4. 启动 Worker.poll
5. 暴露 `/health`（HTTP）、`/metrics`（Prometheus）

## 优雅关闭
SIGTERM → 停止 polling → 等当前 Activity 完成（最多 60s）→ 超时让 Activity 心跳停 → Temporal 重试到副本 → 退出。

## 边界
- 只在这里 import 上游所有模块
- 不在此处实现业务（业务在 workflows / activities）

## 测试策略
- `tests/integration/test_worker_lifecycle.py`：启动 / 优雅关闭 / startup probe 失败
- `tests/chaos/test_kill_worker.py`：SIGKILL 后任务能在副本上恢复

## 常见陷阱
- 忘了注册 PayloadCodec → 加密字段在 Web UI 漏明文
- startup probe 不严格 → 半启动接到任务后失败
- 没接 SIGTERM → K8s 滚动升级丢任务
