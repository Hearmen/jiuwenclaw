# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""v_cc 权限评估与 permission rail 工具名收集的单元测试."""

from jiuwenclaw.agentserver.permissions.checker import collect_permission_rail_tool_names
from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.permissions.v_cc import (
    evaluate_v_cc,
    permissions_schema_is_v_cc,
    severity_to_decision,
    strictest,
)


def test_schema_detection():
    assert permissions_schema_is_v_cc({"schema": "v_cc"})
    assert permissions_schema_is_v_cc({"version": "V_CC"})
    assert permissions_schema_is_v_cc({"schema": "v4.2"})
    assert not permissions_schema_is_v_cc({})
    assert not permissions_schema_is_v_cc({"schema": "legacy"})


def test_severity_mapping_normal():
    assert severity_to_decision("LOW", "normal") == PermissionLevel.ALLOW
    assert severity_to_decision("MEDIUM", "normal") == PermissionLevel.ALLOW
    assert severity_to_decision("HIGH", "normal") == PermissionLevel.ASK
    assert severity_to_decision("CRITICAL", "normal") == PermissionLevel.ASK


def test_severity_mapping_strict():
    assert severity_to_decision("MEDIUM", "strict") == PermissionLevel.ASK
    assert severity_to_decision("CRITICAL", "strict") == PermissionLevel.DENY


def test_strictest_merge_baseline_ask_rule_allow():
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
    perm, _ = evaluate_v_cc(cfg, "mcp_exec_command", {"command": "git status"})
    assert perm == PermissionLevel.ASK


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
    perm, _ = evaluate_v_cc(cfg, "mcp_exec_command", {"command": "rm -rf /tmp/x"})
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
    perm, _ = evaluate_v_cc(cfg, "mcp_exec_command", {"command": "rm -rf /tmp/x"})
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
    perm, _ = evaluate_v_cc(cfg, "mcp_exec_command", {"command": "git status"})
    assert perm == PermissionLevel.DENY


def test_defaults_when_tool_not_in_tools():
    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {},
        "rules": [],
    }
    perm, mr = evaluate_v_cc(cfg, "some_tool", {})
    assert perm == PermissionLevel.ASK
    assert "defaults" in mr


def test_strictest_helper():
    assert strictest(PermissionLevel.ALLOW, PermissionLevel.DENY) == PermissionLevel.DENY


def test_collect_tools_keys_only():
    cfg = {
        "tools": {"mcp_exec_command": "ask", "write": "ask"},
    }
    assert collect_permission_rail_tool_names(cfg) == ["mcp_exec_command", "write"]


def test_collect_merges_rules_tools():
    cfg = {
        "tools": {"mcp_exec_command": "ask"},
        "rules": [
            {"id": "r1", "tools": ["read_file", "write_file"], "pattern": "**/.ssh/**", "severity": "HIGH"},
        ],
    }
    assert collect_permission_rail_tool_names(cfg) == [
        "mcp_exec_command",
        "read_file",
        "write_file",
    ]


def test_collect_rules_only_tools():
    cfg = {
        "rules": [
            {"tools": ["only_in_rules"]},
        ],
    }
    assert collect_permission_rail_tool_names(cfg) == ["only_in_rules"]


def test_collect_dedup_and_sort():
    cfg = {
        "tools": {"zebra": "allow", "alpha": "ask"},
        "rules": [{"tools": ["alpha", "beta"]}],
    }
    assert collect_permission_rail_tool_names(cfg) == ["alpha", "beta", "zebra"]
