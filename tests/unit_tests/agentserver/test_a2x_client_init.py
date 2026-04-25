from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_module
from jiuwenclaw.agentserver.a2x_registry_runtime import (
    clear_blank_registration_cache_for_tests,
    reserve_blank_teammate_agent,
    resolve_a2x_config,
    register_teammate_blank_agent_at_startup,
)
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


class _FakeAsyncA2XRegistryClient:
    instances: list["_FakeAsyncA2XRegistryClient"] = []

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        api_key: str | None,
        ownership_file,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key
        self.ownership_file = ownership_file
        self.blank_registrations: list[dict[str, object]] = []
        self.reservations: list[dict[str, object]] = []
        self.released_reservations: list[str] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def register_blank_agent(
        self,
        dataset: str,
        endpoint: str,
        service_id: str | None = None,
        persistent: bool = True,
    ):
        self.blank_registrations.append(
            {
                "dataset": dataset,
                "endpoint": endpoint,
                "service_id": service_id,
                "persistent": persistent,
            }
        )
        return SimpleNamespace(service_id="blank-service-id")

    async def reserve_blank_agents(
        self,
        dataset: str,
        n: int = 1,
        ttl_seconds: int = 30,
        holder_id: str | None = None,
        extra_filters: dict[str, object] | None = None,
    ):
        self.reservations.append(
            {
                "dataset": dataset,
                "n": n,
                "ttl_seconds": ttl_seconds,
                "holder_id": holder_id,
                "extra_filters": extra_filters,
            }
        )
        return SimpleNamespace(
            holder_id="holder-1",
            agents=[
                {
                    "id": "blank-service-id",
                    "endpoint": "tcp://127.0.0.1:28610",
                    "status": "online",
                }
            ],
        )

    async def release_reservation(self, reservation) -> list[str]:
        self.released_reservations.append(reservation.holder_id)
        return ["blank-service-id"]

    async def aclose(self) -> None:
        self.closed = True


class _FailingAsyncA2XRegistryClient:
    def __init__(self, **_: object) -> None:
        raise RuntimeError("boom")


def _make_config(role: str, *, dataset: str = "", endpoint: str = "") -> dict:
    return {
        "react": {
            "agent_name": "main_agent",
            "workspace_dir": "/tmp/test-workspace",
            "enable_task_loop": True,
            "max_iterations": 3,
            "a2x_registry": {
                "base_url": "http://127.0.0.1:8000",
                "timeout": 30.0,
                "api_key": "",
                "ownership_file": False,
                "role": role,
                "dataset": dataset,
                "endpoint": endpoint,
            },
        },
        "team": {
            "runtime": {
                "mode": "distributed",
                "role": "leader" if role == "teamleader" else role,
            },
        },
        "permissions": {"enabled": True},
    }


def test_teammate_a2x_endpoint_defaults_to_bootstrap_addr() -> None:
    config = _make_config("teammate", dataset="team_pool")
    config["team"] = {
        "transport": {
            "params": {
                "bootstrap_direct_addr": "tcp://0.0.0.0:28610",
            }
        }
    }

    resolved = resolve_a2x_config(config)

    assert resolved["endpoint"] == "tcp://127.0.0.1:28610"


@pytest.mark.asyncio
async def test_create_instance_registers_blank_agent_for_teammate(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_blank_registration_cache_for_tests()
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenclaw.a2x_registry_client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient

    adapter = JiuWenClawDeepAdapter()
    config_base = _make_config(
        "teammate",
        dataset="team_dataset",
        endpoint="http://agent.example/ws",
    )

    monkeypatch.setitem(sys.modules, "jiuwenclaw.a2x_registry_client", fake_module)
    monkeypatch.setattr(interface_module, "get_config", lambda: config_base)

    with (
        patch.object(interface_module.JiuWenClawDeepAdapter, "set_checkpoint", AsyncMock()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_refresh_multimodal_configs", return_value=None),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_create_model", return_value=object()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_get_tool_cards", AsyncMock(return_value=[])),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_build_agent_rails", return_value=[]),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_create_sys_operation", return_value=MagicMock()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_build_configured_subagents", return_value=None),
        patch.object(interface_module.JiuWenClawDeepAdapter, "load_user_rails", AsyncMock()),
        patch.object(interface_module, "init_permission_engine", return_value=None),
        patch.object(interface_module, "create_deep_agent", return_value=MagicMock(name="deep_agent")),
    ):
        await adapter.create_instance()

    assert len(_FakeAsyncA2XRegistryClient.instances) == 1
    assert _FakeAsyncA2XRegistryClient.instances[0].blank_registrations == [
        {
            "dataset": "team_dataset",
            "endpoint": "http://agent.example/ws",
            "service_id": None,
            "persistent": True,
        }
    ]


@pytest.mark.asyncio
async def test_startup_registers_blank_agent_without_deepagent(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_blank_registration_cache_for_tests()
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenclaw.a2x_registry_client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenclaw.a2x_registry_client", fake_module)

    registered = await register_teammate_blank_agent_at_startup(
        _make_config(
            "teammate",
            dataset="team_pool",
            endpoint="tcp://127.0.0.1:28610",
        ),
        source="test-startup",
    )

    assert registered is True
    assert len(_FakeAsyncA2XRegistryClient.instances) == 1
    assert _FakeAsyncA2XRegistryClient.instances[0].closed is True
    assert _FakeAsyncA2XRegistryClient.instances[0].blank_registrations == [
        {
            "dataset": "team_pool",
            "endpoint": "tcp://127.0.0.1:28610",
            "service_id": None,
            "persistent": True,
        }
    ]


@pytest.mark.asyncio
async def test_leader_reserves_blank_teammate_from_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_blank_registration_cache_for_tests()
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenclaw.a2x_registry_client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenclaw.a2x_registry_client", fake_module)

    reserved = await reserve_blank_teammate_agent(
        _make_config("teamleader", dataset="team_pool"),
        source="test-leader",
    )

    assert reserved is not None
    assert reserved.service_id == "blank-service-id"
    assert reserved.endpoint == "tcp://127.0.0.1:28610"
    assert _FakeAsyncA2XRegistryClient.instances[0].reservations == [
        {
            "dataset": "team_pool",
            "n": 1,
            "ttl_seconds": 30,
            "holder_id": None,
            "extra_filters": None,
        }
    ]
    await reserved.release()
    await reserved.close()
    assert _FakeAsyncA2XRegistryClient.instances[0].released_reservations == ["holder-1"]
    assert _FakeAsyncA2XRegistryClient.instances[0].closed is True


@pytest.mark.asyncio
async def test_create_instance_continues_when_a2x_client_init_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("jiuwenclaw.a2x_registry_client")
    fake_module.AsyncA2XRegistryClient = _FailingAsyncA2XRegistryClient

    adapter = JiuWenClawDeepAdapter()
    config_base = _make_config("teamleader")

    monkeypatch.setitem(sys.modules, "jiuwenclaw.a2x_registry_client", fake_module)
    monkeypatch.setattr(interface_module, "get_config", lambda: config_base)

    created_instance = MagicMock(name="deep_agent")

    with (
        patch.object(interface_module.JiuWenClawDeepAdapter, "set_checkpoint", AsyncMock()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_refresh_multimodal_configs", return_value=None),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_create_model", return_value=object()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_get_tool_cards", AsyncMock(return_value=[])),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_build_agent_rails", return_value=[]),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_create_sys_operation", return_value=MagicMock()),
        patch.object(interface_module.JiuWenClawDeepAdapter, "_build_configured_subagents", return_value=None),
        patch.object(interface_module.JiuWenClawDeepAdapter, "load_user_rails", AsyncMock()),
        patch.object(interface_module, "init_permission_engine", return_value=None),
        patch.object(interface_module, "create_deep_agent", return_value=created_instance) as create_agent_mock,
    ):
        await adapter.create_instance()

    create_agent_mock.assert_called_once()
