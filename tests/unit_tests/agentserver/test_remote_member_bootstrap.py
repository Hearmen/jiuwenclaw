# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_root = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "_jiuwen_remote_member_bootstrap_test",
    _root / "jiuwenclaw" / "agentserver" / "team" / "remote_member_bootstrap.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
remote_member_names = _mod.remote_member_names
remote_all_spawn_members = _mod.remote_all_spawn_members
parse_remote_bootstrap_ack_json = _mod.parse_remote_bootstrap_ack_json
build_bootstrap_ack_envelope = _mod.build_bootstrap_ack_envelope
attach_remote_bootstrap_ack_listener = _mod.attach_remote_bootstrap_ack_listener
attach_distributed_local_spawn_guard = _mod.attach_distributed_local_spawn_guard


def test_remote_member_names_accepts_string():
    cfg = {"team": {"metadata": {"jiuwen_remote_member_names": "  t1  "}}}
    assert remote_member_names(cfg) == {"t1"}


def test_remote_member_names_accepts_list():
    cfg = {"team": {"metadata": {"jiuwen_remote_member_names": ["a", "b", ""]}}}
    assert remote_member_names(cfg) == {"a", "b"}


def test_remote_member_names_empty_when_missing():
    assert remote_member_names({"team": {}}) == set()


def test_remote_all_spawn_members_true_in_distributed_by_default():
    cfg = {"team": {"runtime": {"mode": "distributed"}}}
    assert remote_all_spawn_members(cfg) is True


def test_remote_all_spawn_members_honors_metadata_override():
    cfg = {
        "team": {
            "runtime": {"mode": "distributed"},
            "metadata": {"jiuwen_remote_all_spawn_members": False},
        },
    }
    assert remote_all_spawn_members(cfg) is False


def test_parse_remote_bootstrap_ack_json_accepts_valid():
    body = json.dumps(build_bootstrap_ack_envelope(member_name="m1", team_name="t1"))
    parsed = parse_remote_bootstrap_ack_json(body)
    assert parsed is not None
    assert parsed["member_name"] == "m1"
    assert parsed.get("team_name") == "t1"


def test_parse_remote_bootstrap_ack_json_rejects_non_json():
    assert parse_remote_bootstrap_ack_json("not json") is None


@pytest.mark.asyncio
async def test_ack_listener_updates_db_and_marks_read(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {
            "team": {
                "runtime": {"mode": "distributed", "role": "leader"},
                "metadata": {"jiuwen_remote_member_names": ["remote1"]},
            }
        },
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(build_bootstrap_ack_envelope(member_name="remote1", team_name="tn")),
            from_member_name="remote1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    assert len(listeners) == 1

    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-1",
            "from_member_name": "remote1",
            "to_member_name": "leader1",
            "team_name": "tn",
        },
    )
    await listeners[0](ev)

    db.get_message.assert_awaited_once_with("mid-1")
    db.update_member_status.assert_awaited_once_with("remote1", "tn", "ready")
    mm.mark_message_read.assert_awaited_once_with("mid-1", "leader1")


@pytest.mark.asyncio
async def test_ack_listener_ignores_plain_text_message(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {
            "team": {
                "runtime": {"mode": "distributed", "role": "leader"},
                "metadata": {"jiuwen_remote_member_names": ["remote1"]},
            }
        },
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content="hello leader",
            from_member_name="remote1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-2",
            "from_member_name": "remote1",
            "to_member_name": "leader1",
        },
    )
    await listeners[0](ev)

    db.get_message.assert_awaited_once_with("mid-2")
    db.update_member_status.assert_not_awaited()
    mm.mark_message_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_listener_accepts_any_sender_when_remote_all(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(build_bootstrap_ack_envelope(member_name="calculator-1", team_name="tn")),
            from_member_name="calculator-1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-3",
            "from_member_name": "calculator-1",
            "to_member_name": "leader1",
        },
    )
    await listeners[0](ev)

    db.update_member_status.assert_awaited_once_with("calculator-1", "tn", "ready")


@pytest.mark.asyncio
async def test_distributed_local_spawn_guard_disables_local_startup(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    send_message_tool = SimpleNamespace(_on_teammate_created=object())
    resource_mgr = MagicMock()
    resource_mgr.get_tool = MagicMock(return_value=send_message_tool)
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)

    original_spawn = AsyncMock(return_value="local-handle")
    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        deep_agent=SimpleNamespace(
            ability_manager=SimpleNamespace(
                list=lambda: [SimpleNamespace(id="team.send_message", name="send_message")]
            ),
            card=SimpleNamespace(id="leader-card"),
        ),
        spawn_teammate=original_spawn,
    )

    attach_distributed_local_spawn_guard(ta, session_id="sid", channel_id="web")

    assert getattr(send_message_tool, "_on_teammate_created") is None
    assert getattr(ta, "_jiuwen_distributed_local_spawn_guard_attached") is True
    result = await ta.spawn_teammate(SimpleNamespace(member_name="calculator-1"))
    assert result is None
    original_spawn.assert_not_awaited()
