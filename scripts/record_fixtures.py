"""手动录制 Replay fixture 的脚本。

用法:
  python scripts/record_fixtures.py

前提条件:
  - Temporal Server 在 localhost:7233 运行
  - Worker 已启动
  - 至少跑过一次集成测试

本地开发时使用 WorkflowEnvironment in-process 直接生成 fixture，
无需真实 Temporal Server。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "replay" / "fixtures"


async def record_minimal_pipeline():
    """录制最简 2-stage 流水线的 Workflow History。"""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.contrib.pydantic import pydantic_data_converter
    from orchestra.schema.parser import parse_pipeline
    from orchestra.workflows.pipeline_workflow import PipelineRunInput, PipelineWorkflow
    from orchestra.adapters.registry import build_registry, load_profiles_from_yaml
    from orchestra.worker.registry import build_worker
    from orchestra.state.idempotency import init_store
    from orchestra.state.artifact_store import init_artifact_store
    from orchestra.observability.audit import init_audit_writer

    tmp = tempfile.mkdtemp()
    init_store("memory")
    init_artifact_store(tmp + "/artifacts")
    init_audit_writer(tmp + "/audits.db")

    profiles = load_profiles_from_yaml(Path("config/profiles.yaml"))
    build_registry(profiles, use_mock=True, mock_outputs={
        "walnut": {"patch": "def hello(): pass"},
        "almond": {"result": "pass", "coverage": 90.0},
    })

    pipeline = parse_pipeline("examples/minimal.pipeline.yaml")
    pipeline_dict = pipeline.model_dump(by_alias=True)
    wf_id = f"fixture-minimal-{uuid.uuid4().hex[:8]}"

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with build_worker(env.client, "test-queue"):
            handle = await env.client.start_workflow(
                PipelineWorkflow.run,
                PipelineRunInput(
                    pipeline_dict=pipeline_dict,
                    run_id="fixture-001",
                    params={"task": "write hello function"},
                ),
                id=wf_id,
                task_queue="test-queue",
            )
            result = await handle.result()
            assert result["phase"] == "Succeeded"

            # 获取 History 并保存为 fixture
            try:
                history = await handle.fetch_history()
                history_json = history.to_json()
                FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
                out_path = FIXTURES_DIR / "linear_happy.json"
                out_path.write_text(history_json)
                print(f"✓ 已保存 fixture: {out_path}")
                print(f"  Workflow 结果: {result}")
            except Exception as e:
                # in-process WorkflowEnvironment 可能不支持 fetch_history
                # 改为记录到 README，提示后续在真实 Server 录制
                print(f"  fetch_history 不支持当前测试环境: {e}")
                print("  正在写入说明到 README...")
                _create_minimal_fixture(wf_id, result)


def _create_minimal_fixture(workflow_id: str, result: dict) -> None:
    """在测试环境无法录制时，向 README 写入待录制说明。"""
    # Replayer 需要真实的 Event History JSON
    # 测试环境无法生成，记录到 README 提示后续在真实 Server 录制
    readme_path = FIXTURES_DIR / "README.md"
    note = f"""
## 待录制 fixture

以下 fixture 需要在真实 Temporal Server 上录制：

- `linear_happy.json` : minimal pipeline (2 stages) - {workflow_id}

录制方法:
  1. `docker compose -f deploy/docker-compose.yml up -d`
  2. `orchestra submit examples/minimal.pipeline.yaml --param task="hello"`
  3. `temporal workflow show --workflow-id <id> --output json > tests/replay/fixtures/linear_happy.json`
"""
    existing = readme_path.read_text() if readme_path.exists() else ""
    if "待录制 fixture" not in existing:
        with open(readme_path, "a") as f:
            f.write(note)
    print(f"  说明已写入: {readme_path}")


if __name__ == "__main__":
    asyncio.run(record_minimal_pipeline())
