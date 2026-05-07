# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for channel-scoped team manager registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agents.harness.team.team_manager import (
    TeamManager,
    cleanup_team_runtime_state_once,
    get_team_manager,
    reset_team_manager,
    sync_team_skills_across_managers,
)


def setup_function() -> None:
    reset_team_manager()


def teardown_function() -> None:
    reset_team_manager()


def test_get_team_manager_is_scoped_by_channel() -> None:
    web_manager = get_team_manager("web")
    feishu_manager = get_team_manager("feishu")
    web_manager_again = get_team_manager("web")

    assert isinstance(web_manager, TeamManager)
    assert isinstance(feishu_manager, TeamManager)
    assert web_manager is web_manager_again
    assert web_manager is not feishu_manager


@pytest.mark.asyncio
async def test_team_manager_keeps_single_session_per_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed_sessions: list[str] = []
    created_sessions: list[str] = []
    stopped_messagers: list[str] = []

    class _FakeTeamAgent:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self._messager = self._FakeMessager(session_id)

        class _FakeMessager:
            def __init__(self, session_id: str) -> None:
                self.session_id = session_id

            async def stop(self) -> None:
                stopped_messagers.append(self.session_id)

        async def destroy_team(self, force: bool = False) -> bool:
            _ = force
            destroyed_sessions.append(self.session_id)
            return True

    class _FakeWorkspace:
        root_path = None

    def fake_load_team_spec(session_id: str):
        class _Spec:
            team_name = f"team-{session_id}"
            agent_customizer = None
            workspace = _FakeWorkspace()

            @staticmethod
            def build() -> _FakeTeamAgent:
                created_sessions.append(session_id)
                return _FakeTeamAgent(session_id)

        return _Spec()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(fake_load_team_spec))
    # Mock _copy_global_skills_to_team_shared_dir to avoid file operations
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        lambda self, spec: None,
    )

    web_manager = get_team_manager("web")
    feishu_manager = get_team_manager("feishu")

    await web_manager.get_or_create_team("web-s1", deep_agent=object(), channel_id="web")
    await feishu_manager.get_or_create_team("fs-s1", deep_agent=object(), channel_id="feishu")
    await web_manager.get_or_create_team("web-s2", deep_agent=object(), channel_id="web")

    assert created_sessions == ["web-s1", "fs-s1", "web-s2"]
    assert destroyed_sessions == ["web-s1"]
    assert stopped_messagers == ["web-s1"]
    assert web_manager.get_team_agent("web-s1") is None
    assert isinstance(web_manager.get_team_agent("web-s2"), _FakeTeamAgent)
    assert isinstance(feishu_manager.get_team_agent("fs-s1"), _FakeTeamAgent)


@pytest.mark.asyncio
async def test_create_team_does_not_run_global_runtime_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_called = False

    class _FakeWorkspace:
        root_path = None

    def fake_load_team_spec(_session_id: str):
        class _Spec:
            team_name = "demo-team"
            agent_customizer = None
            workspace = _FakeWorkspace()

            @staticmethod
            def build():
                return object()

        return _Spec()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(fake_load_team_spec))
    # Mock _copy_global_skills_to_team_shared_dir to avoid file operations
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        lambda self, spec: None,
    )
    manager = TeamManager()

    async def fail_cleanup(*_args, **_kwargs):
        nonlocal cleanup_called
        cleanup_called = True
        raise AssertionError("global runtime cleanup should not run during create_team")

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.cleanup_team_runtime_state_once",
        fail_cleanup,
    )

    team_agent = await manager.create_team("sess-1", deep_agent=object(), channel_id="web")

    assert team_agent is not None
    assert cleanup_called is False
    assert manager.get_team_agent("sess-1") is team_agent


@pytest.mark.asyncio
async def test_cleanup_team_runtime_state_once_uses_shared_db(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeSharedDb:
        @staticmethod
        async def cleanup_all_runtime_state():
            return ["dyn_table"], ["static_table"]

    captured: dict[str, object] = {}

    def fake_get_shared_db(db_config):
        captured["db_type"] = db_config.db_type
        captured["connection_string"] = db_config.connection_string
        return _FakeSharedDb()

    monkeypatch.setattr(
        "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
        fake_get_shared_db,
    )

    deleted_tables, cleared_tables = await cleanup_team_runtime_state_once()

    assert deleted_tables == ["dyn_table"]
    assert cleared_tables == ["static_table"]
    assert captured["db_type"] == "sqlite"
    assert str(captured["connection_string"]).endswith("team.db")


def test_sync_team_skills_across_managers_uses_public_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = get_team_manager("web")
    source = Path("/tmp/team-source")
    target = Path("/tmp/team-target")
    manager.register_team_skill_sync_target("sess-1", source, target)

    called = {"count": 0}

    def fake_sync(session_id: str) -> None:
        called["count"] += 1
        assert session_id == "sess-1"

    monkeypatch.setattr(manager, "sync_team_skills", fake_sync)

    assert sync_team_skills_across_managers("sess-1") is True
    assert called["count"] == 1
