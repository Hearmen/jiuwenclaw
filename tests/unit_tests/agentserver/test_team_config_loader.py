# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team config loading."""

from jiuwenclaw.agentserver.team.config_loader import load_team_spec_dict


def test_load_team_spec_dict_supports_member_specific_agents(monkeypatch, tmp_path):
    """Predefined members should resolve to member_name-keyed DeepAgentSpec entries."""
    fake_agent_teams_home = tmp_path / ".agent_teams"
    config = {
        "models": {
            "default": {
                "model_client_config": {
                    "model_name": "gpt-test",
                    "client_provider": "openai",
                },
                "model_config_obj": {"temperature": 0.2},
            }
        },
        "team": {
            "team_name": "demo_team",
            "leader": {
                "member_name": "team_leader",
                "display_name": "TeamLeader",
                "persona": "Lead the team",
            },
            "workspace": {
                "enabled": True,
                "artifact_dirs": ["artifacts/reports"],
            },
            "agents": {
                "leader": {
                },
                "teammate": {
                },
                "analyst": {
                    "name": "Analyst",
                    "skills": ["skill-a", "skill-b"],
                },
            },
            "predefined_members": [
                {
                    "member_name": "analyst",
                    "display_name": "Data Analyst",
                    "persona": "Analyze data",
                }
            ],
            "storage": {
                "type": "sqlite",
                "params": {
                    "connection_string": "team.db",
                },
            },
        },
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: fake_agent_teams_home,
    )

    spec = load_team_spec_dict("session-1")

    assert spec["team_name"] == "demo_team_session-1"
    assert spec["leader"]["member_name"] == "team_leader"
    assert spec["leader"]["display_name"] == "TeamLeader"
    assert spec["leader"]["persona"] == "Lead the team"
    assert spec["predefined_members"][0]["member_name"] == "analyst"
    assert spec["predefined_members"][0]["display_name"] == "Data Analyst"
    assert spec["workspace"]["enabled"] is True
    assert spec["workspace"]["artifact_dirs"] == ["artifacts/reports"]
    assert spec["agents"]["analyst"]["skills"] == ["skill-a", "skill-b"]
    assert spec["agents"]["analyst"]["model"]["model_request_config"]["model"] == "gpt-test"
    assert spec["agents"]["analyst"]["workspace"] == {"stable_base": True}
    assert spec["storage"]["params"]["connection_string"] == str(
        fake_agent_teams_home / "team.db"
    )


def test_load_team_spec_dict_keeps_role_defaults_when_member_alias_is_added(monkeypatch, tmp_path):
    """Role keys should remain usable after member_name aliases are injected."""
    config = {
        "models": {
            "default": {
                "model_client_config": {
                    "model_name": "gpt-role",
                    "client_provider": "openai",
                },
                "model_config_obj": {},
            }
        },
        "team": {
            "agents": {
                "leader": {},
                "teammate": {
                    "skills": ["shared-skill"],
                },
                "default_teammate": {
                    "skills": ["member-skill"],
                },
            }
        },
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: tmp_path / ".agent_teams",
    )

    spec = load_team_spec_dict("session-2")

    assert "leader" in spec["agents"]
    assert "teammate" in spec["agents"]
    assert "default_teammate" in spec["agents"]
    assert spec["agents"]["default_teammate"]["skills"] == ["member-skill"]
    assert spec["agents"]["teammate"]["skills"] == ["shared-skill"]


def test_load_team_spec_dict_preserves_explicit_empty_skills(monkeypatch, tmp_path):
    """Explicit empty skill lists should not be treated as missing config."""
    config = {
        "models": {
            "default": {
                "model_client_config": {
                    "model_name": "gpt-empty",
                    "client_provider": "openai",
                },
                "model_config_obj": {},
            }
        },
        "team": {
            "agents": {
                "leader": {},
                "reviewer": {
                    "skills": [],
                },
            }
        },
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: tmp_path / ".agent_teams",
    )

    spec = load_team_spec_dict("session-3")

    assert "reviewer" in spec["agents"]
    assert spec["agents"]["reviewer"]["skills"] == []
