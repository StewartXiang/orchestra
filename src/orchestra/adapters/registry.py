"""Profile → AgentAdapter 工厂（注册表）。

Worker 启动时调用 build_registry(profiles)，后续通过 get_adapter(name) 获取实例。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..domain.agent import Profile
from ..domain.enums import Role
from .base import AgentAdapter
from .mcp import MCPAdapter
from .mock import MockAgentAdapter, MockBehavior

_REGISTRY: dict[str, AgentAdapter] = {}


def build_registry(
    profiles: dict[str, Profile],
    *,
    use_mock: bool = False,
    mock_behavior: MockBehavior = MockBehavior.SUCCESS,
    mock_outputs: dict[str, Any] | None = None,
) -> dict[str, AgentAdapter]:
    """从 Profile 字典构建 Adapter 注册表。

    :param profiles: {profile_name -> Profile}
    :param use_mock: 为 True 时全部使用 MockAdapter（CI / 集成测试）
    :param mock_behavior: Mock 行为（仅 use_mock=True 时有效）
    :param mock_outputs: {profile_name -> output dict}（仅 use_mock=True 时有效）
    """
    global _REGISTRY
    registry: dict[str, AgentAdapter] = {}

    for name, profile in profiles.items():
        if use_mock:
            registry[name] = MockAgentAdapter(
                profile_name=name,
                role=profile.role,
                capabilities=profile.capabilities,
                tools=profile.tools,
                behavior=mock_behavior,
                output=(mock_outputs or {}).get(name, {}),
            )
        else:
            registry[name] = MCPAdapter(
                profile_name=name,
                mcp_endpoint=profile.mcpEndpoint,
                allowed_tools=profile.tools,
                role=profile.role,
            )

    _REGISTRY = registry
    return registry


def get_adapter(profile_name: str) -> AgentAdapter:
    """获取 Adapter 实例。

    :raises KeyError: profile 未注册
    """
    if profile_name not in _REGISTRY:
        raise KeyError(
            f"Agent profile '{profile_name}' 未注册，"
            f"已注册: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[profile_name]


def load_profiles_from_yaml(path: str | Path) -> dict[str, Profile]:
    """从 config/profiles.yaml 加载 Profile 字典。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    profiles: dict[str, Profile] = {}
    for name, raw in data.get("profiles", {}).items():
        raw["name"] = name
        profiles[name] = Profile.model_validate(raw)
    return profiles


def list_adapter_names() -> list[str]:
    """返回当前注册表中所有 adapter 名称。"""
    return list(_REGISTRY.keys())


def get_profile_task_queue(profile_name: str) -> str:
    """获取 Agent 的 task_queue（若 profile 未指定则推导）。"""
    return f"agent-{profile_name}"


def find_adapter_by_selector(
    role: str | None = None,
    capabilities: list[str] | None = None,
) -> AgentAdapter | None:
    """按 role + capabilities 路由到第一个匹配的 Adapter。"""
    for name, adapter in _REGISTRY.items():
        # 依赖运行时能力，优先取 mock
        if isinstance(adapter, MockAgentAdapter):
            profile_role = adapter.role.value
            profile_caps = set(adapter.capabilities)
        elif isinstance(adapter, MCPAdapter):
            profile_role = adapter._role.value
            profile_caps = set()  # MCP 能力需运行时查询，静态路由跳过
        else:
            continue

        if role and profile_role != role:
            continue
        if capabilities and not all(c in profile_caps for c in capabilities):
            continue
        return adapter

    return None
