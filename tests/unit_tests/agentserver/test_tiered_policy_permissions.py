# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tiered_policy 权限评估与 permission rail 工具名收集的单元测试."""

import asyncio
import ast
import importlib
from pathlib import Path

import pytest
import yaml

from jiuwenclaw.agentserver.permissions.checker import collect_permission_rail_tool_names
from jiuwenclaw.agentserver.permissions.core import PermissionEngine, set_permission_engine
from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.permissions.owner_scopes import _get_global_tool_level
from jiuwenclaw.agentserver.permissions.patterns import persist_permission_allow_rule
from jiuwenclaw.agentserver.permissions.tiered_policy import (
    collect_builtin_permission_rail_tool_names,
    evaluate_tiered_policy,
    permissions_schema_is_tiered_policy,
    severity_to_decision,
    strictest,
)


def test_schema_detection():
    assert permissions_schema_is_tiered_policy({"schema": "tiered_policy"})
    assert permissions_schema_is_tiered_policy({"schema": "TIERED_POLICY"})
    assert permissions_schema_is_tiered_policy({"schema": "v_cc"})
    assert permissions_schema_is_tiered_policy({"version": "V_CC"})
    assert permissions_schema_is_tiered_policy({"schema": "v4.2"})
    assert not permissions_schema_is_tiered_policy({})
    assert not permissions_schema_is_tiered_policy({"schema": "legacy"})


def test_severity_mapping_normal():
    assert severity_to_decision("LOW", "normal") == PermissionLevel.ALLOW
    assert severity_to_decision("MEDIUM", "normal") == PermissionLevel.ALLOW
    assert severity_to_decision("HIGH", "normal") == PermissionLevel.ASK
    assert severity_to_decision("CRITICAL", "normal") == PermissionLevel.ASK


def test_severity_mapping_strict():
    assert severity_to_decision("MEDIUM", "strict") == PermissionLevel.ASK
    assert severity_to_decision("CRITICAL", "strict") == PermissionLevel.DENY


def test_param_rule_overrides_baseline_ask():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"mcp_exec_command": "ask"},
        "rules": [
            {
                "id": "git_low",
                "tools": ["mcp_exec_command"],
                "pattern": "git status *",
                "severity": "LOW",
            },
        ],
    }
    perm, _ = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "git status"})
    assert perm == PermissionLevel.ALLOW


def test_strictest_baseline_allow_rule_critical_strict():
    cfg = {
        "permission_mode": "strict",
        "defaults": {"*": "allow"},
        "tools": {"mcp_exec_command": "allow"},
        "rules": [
            {
                "id": "rm_rf",
                "tools": ["mcp_exec_command"],
                "pattern": "re:^rm\\s+-rf\\b.*$",
                "severity": "CRITICAL",
            },
        ],
    }
    perm, _ = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "rm -rf /tmp/x"})
    assert perm == PermissionLevel.DENY


def test_strictest_baseline_allow_rule_critical_normal():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"mcp_exec_command": "allow"},
        "rules": [
            {
                "id": "rm_rf",
                "tools": ["mcp_exec_command"],
                "pattern": "re:^rm\\s+-rf\\b.*$",
                "severity": "CRITICAL",
            },
        ],
    }
    perm, _ = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "rm -rf /tmp/x"})
    assert perm == PermissionLevel.ASK


def test_baseline_deny_ignores_looser_rule():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"mcp_exec_command": "deny"},
        "rules": [
            {
                "id": "low",
                "tools": ["mcp_exec_command"],
                "pattern": "git status *",
                "severity": "LOW",
            },
        ],
    }
    perm, _ = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "git status"})
    assert perm == PermissionLevel.DENY


def test_defaults_when_tool_not_in_tools():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {},
        "rules": [],
    }
    perm, mr = evaluate_tiered_policy(cfg, "some_tool", {})
    assert perm == PermissionLevel.ASK
    assert "defaults" in mr


def test_strictest_helper():
    assert strictest(PermissionLevel.ALLOW, PermissionLevel.DENY) == PermissionLevel.DENY


def test_whole_tool_allow_ignores_default_ask():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {"mcp_exec_command": "allow"},
        "rules": [],
    }
    perm, mr = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "unknown-cmd-xyz"})
    assert perm == PermissionLevel.ALLOW
    assert "defaults" not in mr


def test_builtin_hits_ignore_user_rules():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"mcp_exec_command": "allow"},
        "rules": [
            {
                "id": "user_fake_allow_all",
                "tools": ["mcp_exec_command"],
                "pattern": "re:.*",
                "severity": "LOW",
            },
        ],
    }
    perm, mr = evaluate_tiered_policy(cfg, "mcp_exec_command", {"command": "rm -rf /tmp/x"})
    assert perm == PermissionLevel.ASK
    assert "builtin" in mr
    assert "user_fake_allow_all" not in mr


def test_collect_tools_keys_only():
    cfg = {
        "tools": {"mcp_exec_command": "ask", "write": "ask"},
    }
    bi = set(collect_builtin_permission_rail_tool_names())
    assert collect_permission_rail_tool_names(cfg) == sorted(
        {"mcp_exec_command", "write"} | bi
    )


def test_collect_merges_rules_tools():
    cfg = {
        "tools": {"mcp_exec_command": "ask"},
        "rules": [
            {"id": "r1", "tools": ["read_file", "write_file"], "pattern": "**/.ssh/**", "severity": "HIGH"},
        ],
    }
    bi = set(collect_builtin_permission_rail_tool_names())
    assert collect_permission_rail_tool_names(cfg) == sorted(
        {"mcp_exec_command", "read_file", "write_file"} | bi
    )


def test_collect_rules_only_tools():
    cfg = {
        "rules": [
            {"tools": ["only_in_rules"]},
        ],
    }
    bi = set(collect_builtin_permission_rail_tool_names())
    assert collect_permission_rail_tool_names(cfg) == sorted({"only_in_rules"} | bi)


def test_collect_dedup_and_sort():
    cfg = {
        "tools": {"zebra": "allow", "alpha": "ask"},
        "rules": [{"tools": ["alpha", "beta"]}],
    }
    bi = set(collect_builtin_permission_rail_tool_names())
    assert collect_permission_rail_tool_names(cfg) == sorted(
        {"alpha", "beta", "zebra"} | bi
    )


def test_approval_override_can_bypass_ask():
    cfg = {
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "ask"},
        "rules": [
            {
                "id": "cp_requires_approval",
                "tools": ["bash"],
                "pattern": "cp src.txt dst.txt",
                "severity": "HIGH",
            },
        ],
        "approval_overrides": [
            {
                "id": "user_allow_cp_src_dst",
                "tools": ["bash"],
                "match_type": "command",
                "pattern": "cp src.txt dst.txt",
                "action": "allow",
                "source": "user_approval",
            },
        ],
    }
    perm, mr = evaluate_tiered_policy(cfg, "bash", {"command": "cp src.txt dst.txt"})
    assert perm == PermissionLevel.ALLOW
    assert "approval_overrides" in mr


def test_approval_override_cannot_bypass_deny():
    cfg = {
        "schema": "tiered_policy",
        "permission_mode": "strict",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [
            {
                "id": "rm_is_deny",
                "tools": ["bash"],
                "pattern": "rm project.txt",
                "severity": "CRITICAL",
            },
        ],
        "approval_overrides": [
            {
                "id": "user_allow_rm_project_txt",
                "tools": ["bash"],
                "match_type": "command",
                "pattern": "rm project.txt",
                "action": "allow",
                "source": "user_approval",
            },
        ],
    }
    perm, mr = evaluate_tiered_policy(cfg, "bash", {"command": "rm project.txt"})
    assert perm == PermissionLevel.DENY
    assert "approval_overrides" not in mr


def test_evaluate_global_policy_directly_uses_tiered_policy_even_when_disabled():
    engine = PermissionEngine({
        "enabled": False,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "ask"},
        "rules": [
            {
                "id": "cp_requires_approval",
                "tools": ["bash"],
                "pattern": "cp src.txt dst.txt",
                "severity": "HIGH",
            },
        ],
    })
    perm, mr = engine.evaluate_global_policy_directly(
        "bash",
        {"command": "cp src.txt dst.txt"},
        "feishu",
        include_external_directory=False,
    )
    assert perm == PermissionLevel.ASK
    assert "rules" in mr


def test_owner_scope_global_level_sees_approval_overrides():
    engine = PermissionEngine({
        "enabled": False,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {"read_file": "ask"},
        "approval_overrides": [
            {
                "id": "user_allow_read_file_a",
                "tools": ["read_file"],
                "match_type": "path",
                "pattern": "/tmp/a.txt",
                "action": "allow",
                "source": "user_approval",
            },
        ],
    })
    set_permission_engine(engine)
    level = asyncio.run(
        _get_global_tool_level(
            engine,
            "read_file",
            {"path": "/tmp/a.txt"},
            "feishu",
            None,
        )
    )
    assert level == "allow"


def test_persist_permission_allow_rule_writes_bash_approval_override(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "permissions": {
                "schema": "tiered_policy",
                "enabled": True,
                "permission_mode": "normal",
                "defaults": {"*": "allow"},
                "tools": {"bash": "ask"},
                "rules": [],
            },
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JIUWENCLAW_CONFIG_DIR", str(tmp_path))
    config_module = importlib.import_module("jiuwenclaw.config")

    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("bash", {"command": "git status"})

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    perms = saved["permissions"]
    assert "approval_overrides" in perms
    assert perms["approval_overrides"][0]["tools"] == ["bash"]
    assert perms["approval_overrides"][0]["pattern"] == "git status"
    assert perms["approval_overrides"][0]["action"] == "allow"
    assert perms["tools"]["bash"] == "ask"


def test_persist_permission_allow_rule_writes_file_approval_override(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "permissions": {
                "schema": "tiered_policy",
                "enabled": True,
                "permission_mode": "normal",
                "defaults": {"*": "allow"},
                "tools": {"read_file": "ask"},
                "rules": [],
            },
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JIUWENCLAW_CONFIG_DIR", str(tmp_path))
    config_module = importlib.import_module("jiuwenclaw.config")

    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("read_file", {"path": "/workspace/docs/a.md"})

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    perms = saved["permissions"]
    assert perms["approval_overrides"][0]["tools"] == ["read_file"]
    assert perms["approval_overrides"][0]["match_type"] == "path"
    assert perms["approval_overrides"][0]["pattern"] == "/workspace/docs/a.md"


def test_default_config_shell_rules_are_consistent():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    config = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))
    rules = config["permissions"]["rules"]

    severities = {}
    for rule in rules:
        pattern = rule.get("pattern")
        tools = tuple(rule.get("tools") or [])
        severity = rule.get("severity")
        if pattern in {"rm *", "del *", "rd *", "mv *", "cp *", "chmod *", "chown *"}:
            severities[(pattern, tools)] = severity

    for pattern in {"rm *", "del *", "rd *", "mv *", "cp *", "chmod *", "chown *"}:
        key = (pattern, ("bash", "mcp_exec_command"))
        assert severities.get(key) == "HIGH"


def test_default_config_denies_sensitive_shell_file_reads():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))["permissions"]

    perm, _ = evaluate_tiered_policy(permissions, "bash", {"command": "cat ~/.ssh/id_rsa"})
    assert perm == PermissionLevel.DENY


def test_default_config_denies_sensitive_file_tools():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))["permissions"]

    perm, _ = evaluate_tiered_policy(permissions, "read_file", {"path": "/workspace/.env"})
    assert perm == PermissionLevel.DENY


def test_builtin_rules_block_system_control_commands():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = {
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [],
    }

    for command in ("shutdown now", "reboot", "systemctl stop sshd", "kill -9 1234"):
        perm, _ = evaluate_tiered_policy(permissions, "bash", {"command": command})
        assert perm == PermissionLevel.DENY


def test_deny_shell_rule_does_not_persist_always_allow(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "permissions": {
                "schema": "tiered_policy",
                "enabled": True,
                "permission_mode": "normal",
                "defaults": {"*": "allow"},
                "tools": {"bash": "ask"},
                "rules": [
                    {
                        "id": "shell_sensitive",
                        "tools": ["bash"],
                        "pattern": r"re:(?i).*\.ssh/.*",
                        "action": "deny",
                    },
                ],
            },
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JIUWENCLAW_CONFIG_DIR", str(tmp_path))
    config_module = importlib.import_module("jiuwenclaw.config")
    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("bash", {"command": "cat ~/.ssh/id_rsa"})

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "approval_overrides" not in saved["permissions"]


def test_default_action_deny_rule_does_not_persist_always_allow(tmp_path, monkeypatch):
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    default_permissions = yaml.safe_load(
        (resources_dir / "config.yaml").read_text(encoding="utf-8")
    )["permissions"]
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"permissions": default_permissions}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JIUWENCLAW_CONFIG_DIR", str(tmp_path))
    config_module = importlib.import_module("jiuwenclaw.config")
    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("bash", {"command": "cat ~/.ssh/id_rsa"})

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "approval_overrides" not in saved["permissions"]


def test_permission_rail_before_tool_call_applies_interrupt_decision():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "rails"
        / "permission_rail.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    before_tool_call = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PermissionInterruptRail":
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "before_tool_call":
                    before_tool_call = child
                    break

    assert before_tool_call is not None

    method_src = ast.get_source_segment(module_path.read_text(encoding="utf-8"), before_tool_call)
    assert "self._apply_decision(ctx, tool_call, tool_name, decision)" in method_src
    assert "user_input=user_input" in method_src
    assert "user_input=None" not in method_src
