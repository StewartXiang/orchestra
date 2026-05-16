"""Replay 兼容性回归测试。

工作原理：
  1. 从 tests/replay/fixtures/*.json 加载已录制的 Workflow History
  2. 用 temporalio.worker.Replayer 跑 Replay
  3. 任一失败 → 说明 Workflow 代码引入了非确定性变更 → 阻止合并

如何录制 fixture：
  python scripts/record_fixtures.py
  或 `temporal workflow show -w <id> --output json > fixtures/xxx.json`

CI 要求：
  - Workflow 代码变更后 MUST 跑此目录
  - 失败必须添加 workflow.get_version() 兼容补丁，不能删 fixture
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixtures() -> list[tuple[str, str]]:
    """加载 fixtures/ 下所有 .json history 文件（返回 (name, json_str) 列表）。"""
    fixtures = []
    for f in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            content = f.read_text()
            json.loads(content)  # 验证 JSON 有效
            fixtures.append((f.stem, content))
        except Exception:
            pass
    return fixtures


def _temporal_available() -> bool:
    try:
        from temporalio.worker import Replayer  # noqa: F401
        from temporalio.client import WorkflowHistory  # noqa: F401
        return True
    except ImportError:
        return False


skip_no_temporal = pytest.mark.skipif(
    not _temporal_available(),
    reason="temporalio not installed"
)

_FIXTURES = _load_fixtures()


@pytest.mark.parametrize("name,history_json", _FIXTURES or [("__no_fixtures__", "{}")])
@skip_no_temporal
@pytest.mark.replay
def test_workflow_replay(name: str, history_json: str) -> None:
    """用 Replayer 回放每个 fixture。"""
    if name == "__no_fixtures__":
        pytest.skip("No replay fixtures — run `python scripts/record_fixtures.py`")

    from temporalio.worker import Replayer
    from temporalio.client import WorkflowHistory
    from orchestra.workflows import ALL_WORKFLOWS

    async def _replay() -> None:
        history = WorkflowHistory.from_json(name, history_json)
        replayer = Replayer(workflows=ALL_WORKFLOWS)
        result = await replayer.replay_workflow(history)
        if result.replay_failure is not None:
            raise result.replay_failure

    asyncio.run(_replay())
