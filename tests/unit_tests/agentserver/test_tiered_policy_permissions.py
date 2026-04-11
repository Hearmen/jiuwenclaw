# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tiered_policy 权限评估与 permission rail 工具名收集的单元测试."""

from jiuwenclaw.agentserver.permissions.checker import collect_permission_rail_tool_names
from jiuwenclaw.agentserver.permissions.models import PermissionLevel
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
