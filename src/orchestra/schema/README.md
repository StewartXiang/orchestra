# schema/

## 职责
将 YAML 文件 → 校验 → 转化为 `domain.Pipeline` 对象。**单向流**：YAML → parser → validator → DAG 检查 → renderer，下游只接受 `Pipeline` 对象，不接受裸 dict。

## 关键文件

| 文件 | 责任 |
|---|---|
| `parser.py` | YAML 加载（PyYAML safe_load）+ 简单结构提取 |
| `validator.py` | JSON Schema 校验（jsonschema 2020-12） + 业务校验聚合 |
| `dag.py` | Kahn 拓扑排序 / 环检测 / 孤儿节点 / 子流水线递归 |
| `jsonpath.py` | input/output JSONPath 解析 + 写入路径校验（Stage 不能写到别人的 output）|
| `expr.py` | `condition` 表达式（CEL 沙箱），运算符仅 `==/!=/</>/and/or/not/in/matches` |
| `template.py` | `{{ params.* }}` / `{{ inputs.* }}` 占位符渲染 |

## 静态校验清单（validator 必跑）

1. JSON Schema（含 `unevaluatedProperties: false`）
2. apiVersion 兼容
3. DAG 环检测
4. 孤儿节点检测
5. Agent 引用完整性
6. JSONPath 数据流（input 必须有上游写入）
7. 工具白名单
8. 密钥引用完整性
9. 超时合理性 `heartbeat < startToClose < scheduleToClose < global.timeouts.workflowExecution`
10. 子流水线递归
11. 资源配额
12. 参数占位符完整性
13. DNS-1123 命名
14. 补偿动作引用完整性
15. capability 词表（必须在 `config/capabilities.yaml` 内）

## 边界
- 不调 Temporal API；不发请求；不写文件
- 不在此处生成 Temporal Workflow ID（属于 `worker.registry`）

## 测试策略
- `tests/unit/test_validator.py`：valid + invalid 各 1 条覆盖每条规则
- `tests/unit/test_dag.py`：典型 DAG 模式覆盖
- `tests/unit/test_expr.py`：表达式沙箱安全性（拒绝任意代码）
- 维护 `tests/fixtures/yaml/{valid,invalid}/*.yaml`

## 常见陷阱
- 不要在 schema 之外的代码里"补"默认值 → 全部默认值集中在 JSON Schema
- 不要让 `expr.py` 求值任意 Python 代码 → 必须沙箱
- 占位符渲染要在 schema 校验**之后**做（防 `{{ }}` 让 schema 误判）
