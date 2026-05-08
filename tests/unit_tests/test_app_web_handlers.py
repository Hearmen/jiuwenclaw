# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from jiuwenclaw.app_web_handlers import WebHandlersBindParams, _register_web_handlers


class FakeWebChannel:
    def __init__(self):
        self.methods: dict[str, object] = {}
        self.responses: list[dict] = []
        self.connect_handler = None

    def register_method(self, name, handler):
        self.methods[name] = handler

    def on_connect(self, handler):
        self.connect_handler = handler

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {
                "id": req_id,
                "ok": ok,
                "payload": payload,
                "error": error,
                "code": code,
            }
        )


@pytest.mark.asyncio
async def test_config_set_routes_team_payload_to_modes_team_helper(monkeypatch):
    channel = FakeWebChannel()
    recorded: list[dict] = []

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr("jiuwenclaw.app_web_handlers.get_config_raw", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr("jiuwenclaw.app_web_handlers.get_config", lambda: {"modes": {"team": {}}})
    monkeypatch.setattr(
        "jiuwenclaw.app_web_handlers.replace_teams_in_config",
        lambda payload: recorded.append(payload),
    )

    await channel.methods["config.set"](
        object(),
        "req-1",
        {
            "agents": {"agent_1": {"model": {"provider": "OpenAI"}}},
            "team": [{"team_name": "alpha_team", "leader": {"agent_key": "agent_1"}}],
        },
        "sess-1",
    )

    assert recorded and recorded[0]["team"][0]["team_name"] == "alpha_team"
    assert channel.responses[-1] == {
        "id": "req-1",
        "ok": True,
        "payload": {"updated": ["modes.team"], "applied_without_restart": True},
        "error": None,
        "code": None,
    }


@pytest.mark.asyncio
async def test_config_set_returns_bad_request_when_team_payload_is_invalid(monkeypatch):
    channel = FakeWebChannel()

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr("jiuwenclaw.app_web_handlers.get_config_raw", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(
        "jiuwenclaw.app_web_handlers.replace_teams_in_config",
        lambda payload: (_ for _ in ()).throw(ValueError("duplicate team_name: alpha_team")),
    )

    await channel.methods["config.set"](
        object(),
        "req-2",
        {
            "agents": {"agent_1": {"model": {"provider": "OpenAI"}}},
            "team": [{"team_name": "alpha_team", "leader": {"agent_key": "agent_1"}}],
        },
        "sess-2",
    )

    assert channel.responses[-1] == {
        "id": "req-2",
        "ok": False,
        "payload": None,
        "error": "duplicate team_name: alpha_team",
        "code": "BAD_REQUEST",
    }


@pytest.mark.asyncio
async def test_config_set_routes_security_review_sub_switches(monkeypatch):
    channel = FakeWebChannel()
    recorded: list[tuple[str, bool]] = []

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr("jiuwenclaw.app_web_handlers.get_config_raw", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(
        "jiuwenclaw.app_web_handlers.update_security_review_config_flag",
        lambda key, value: recorded.append((key, value)),
    )

    await channel.methods["config.set"](
        object(),
        "req-3",
        {
            "security_review_runtime_advice": "false",
            "security_review_propose_policy_rules": "true",
        },
        "sess-3",
    )

    assert set(recorded) == {
        ("runtime_advice", False),
        ("propose_policy_rules", True),
    }
    assert channel.responses[-1]["ok"] is True
    assert sorted(channel.responses[-1]["payload"]["updated"]) == [
        "security_review_propose_policy_rules",
        "security_review_runtime_advice",
    ]


@pytest.mark.asyncio
async def test_config_get_returns_security_review_sub_switches(monkeypatch):
    channel = FakeWebChannel()

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr(
        "jiuwenclaw.app_web_handlers.get_config_raw",
        lambda: {
            "react": {
                "security_review": {
                    "enabled": True,
                    "runtime_advice": False,
                    "async_review": True,
                    "evolve_security_skills": False,
                    "propose_policy_rules": False,
                    "timely_tool_failure_review": True,
                }
            }
        },
    )

    await channel.methods["config.get"](object(), "req-4", {}, "sess-4")

    payload = channel.responses[-1]["payload"]
    assert payload["security_review_enabled"] == "true"
    assert payload["security_review_runtime_advice"] == "false"
    assert payload["security_review_async_review"] == "true"
    assert payload["security_review_evolve_security_skills"] == "false"
    assert payload["security_review_propose_policy_rules"] == "false"
    assert payload["security_review_timely_tool_failure_review"] == "true"
