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
from jiuwenclaw.agentserver.permissions.shell_ast import (
    ShellAstParseResult,
    ShellStructureFlags,
    ShellSubcommand,
)
from jiuwenclaw.agentserver.permissions.suggestions import build_shell_permission_suggestions
from jiuwenclaw.agentserver.permissions.tiered_policy import (
    collect_builtin_permission_rail_tool_names,
    evaluate_tiered_policy,
    maybe_escalate_shell_operators,
    permissions_schema_is_tiered_policy,
    severity_to_decision,
    strictest,
)


class _FakeTreeNode:
    def __init__(self, node_type, start_byte=0, end_byte=0, children=None):
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.children = children or []
        self.has_error = False


class _FakeTree:
    def __init__(self, root_node):
        self.root_node = root_node


class _FakeParser:
    def __init__(self, root_node):
        self._root_node = root_node

    def parse(self, _source):
        return _FakeTree(self._root_node)


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


def test_create_terminal_operator_chain_escalates_allow_to_ask():
    assert maybe_escalate_shell_operators(
        "create_terminal",
        {"command": "echo ok && whoami"},
        PermissionLevel.ALLOW,
    ) == PermissionLevel.ASK


def test_shell_ast_fallback_keeps_simple_command(monkeypatch):
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", False)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", None)

    result = shell_ast_module.parse_shell_for_permission("git status")

    assert result.kind == "simple"
    assert [item.text for item in result.subcommands] == ["git status"]


def test_shell_ast_fallback_marks_compound_command_parse_unavailable(monkeypatch):
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", False)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", None)

    result = shell_ast_module.parse_shell_for_permission("git status && rm -rf /tmp/x")

    assert result.kind == "parse_unavailable"
    assert result.flags.has_compound_operators


def test_shell_ast_tree_sitter_extracts_subcommands(monkeypatch):
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    command = "git status && npm test"
    command_one = _FakeTreeNode("command", 0, 10)
    operator = _FakeTreeNode("&&", 11, 13)
    command_two = _FakeTreeNode("command", 14, len(command))
    list_node = _FakeTreeNode("list", 0, len(command), [command_one, operator, command_two])
    root = _FakeTreeNode("program", 0, len(command), [list_node])
    parser = _FakeParser(root)

    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", True)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", parser)

    result = shell_ast_module.parse_shell_for_permission(command)

    assert result.kind == "simple"
    assert result.backend == "tree-sitter"
    assert [item.text for item in result.subcommands] == ["git status", "npm test"]
    assert result.flags.has_compound_operators
    assert result.flags.has_actual_operator_nodes
    assert result.flags.operators == ("&&",)


def test_shell_ast_tree_sitter_marks_command_substitution_too_complex(monkeypatch):
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    command = "echo $(whoami)"
    command_substitution = _FakeTreeNode("command_substitution", 5, len(command))
    command_node = _FakeTreeNode("command", 0, len(command), [command_substitution])
    root = _FakeTreeNode("program", 0, len(command), [command_node])
    parser = _FakeParser(root)

    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", True)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", parser)

    result = shell_ast_module.parse_shell_for_permission(command)

    assert result.kind == "too_complex"
    assert result.backend == "tree-sitter"
    assert result.flags.has_command_substitution


def test_shell_ast_real_tree_sitter_extracts_subcommands(monkeypatch):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_bash")

    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", None)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", None)

    result = shell_ast_module.parse_shell_for_permission("git status && npm test")

    assert result.backend == "tree-sitter"
    assert result.kind == "simple"
    assert [item.text for item in result.subcommands] == ["git status", "npm test"]
    assert result.flags.has_compound_operators
    assert result.flags.has_actual_operator_nodes


def test_shell_ast_real_tree_sitter_marks_command_substitution_too_complex(monkeypatch):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_bash")

    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_BASH_READY", None)
    monkeypatch.setattr(shell_ast_module, "_TREE_SITTER_PARSER", None)

    result = shell_ast_module.parse_shell_for_permission("echo $(whoami)")

    assert result.backend == "tree-sitter"
    assert result.kind == "too_complex"
    assert result.flags.has_command_substitution


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


def test_shell_ast_subcommand_aggregation_hits_stricter_rule(monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.setattr(
        tiered_policy_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="rm -rf /tmp/x"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )

    cfg = {
        "permission_mode": "strict",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [
            {
                "id": "allow_git",
                "tools": ["bash"],
                "pattern": "git status *",
                "severity": "LOW",
            },
            {
                "id": "deny_rm",
                "tools": ["bash"],
                "pattern": "re:^rm\\s+-rf\\b.*$",
                "severity": "CRITICAL",
            },
        ],
    }

    perm, mr = evaluate_tiered_policy(cfg, "bash", {"command": "git status && rm -rf /tmp/x"})
    assert perm == PermissionLevel.DENY
    assert "builtin" in mr or "deny_rm" in mr


def test_shell_ast_subcommand_aggregation_falls_back_for_unmatched_subcommand(monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.setattr(
        tiered_policy_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="echo secret"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )

    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "ask"},
        "rules": [
            {
                "id": "allow_git",
                "tools": ["bash"],
                "pattern": "git status *",
                "severity": "LOW",
            },
        ],
    }

    perm, _mr = evaluate_tiered_policy(cfg, "bash", {"command": "git status && echo secret"})
    assert perm == PermissionLevel.ASK


def test_shell_ast_structure_guard_upgrades_allow_to_ask(monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.setattr(
        tiered_policy_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="simple",
            subcommands=(ShellSubcommand(text="cat notes.txt"),),
            flags=ShellStructureFlags(has_output_redirection=True),
            backend="test",
        ),
    )

    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [
            {
                "id": "allow_cat",
                "tools": ["bash"],
                "pattern": "cat *",
                "severity": "LOW",
            },
        ],
    }

    perm, mr = evaluate_tiered_policy(cfg, "bash", {"command": "cat notes.txt > out.txt"})
    assert perm == PermissionLevel.ASK
    assert "shell_ast" in mr


def test_shell_ast_parse_unavailable_fail_closed(monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.setattr(
        tiered_policy_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="parse_unavailable",
            flags=ShellStructureFlags(has_pipeline=True),
            reason="fallback detected shell structure",
            backend="test",
        ),
    )

    cfg = {
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [
            {
                "id": "allow_git",
                "tools": ["bash"],
                "pattern": "git status *",
                "severity": "LOW",
            },
        ],
    }

    perm, mr = evaluate_tiered_policy(cfg, "bash", {"command": "git status | cat"})
    assert perm == PermissionLevel.ASK
    assert "shell_ast:parse_unavailable" in mr


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


def test_owner_scope_global_level_sees_approval_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JIUWENCLAW_CONFIG_DIR", str(tmp_path))
    engine = PermissionEngine({
        "enabled": False,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {"read_file": "ask"},
        # read_file 会走 ExternalDirectoryChecker；/tmp 在 Windows 上常解析为 C:/tmp/...
        "external_directory": {
            "*": "ask",
            "/tmp": "allow",
            "C:/tmp": "allow",
        },
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
        key = (pattern, ("bash", "mcp_exec_command", "create_terminal"))
        assert severities.get(key) == "HIGH"


def test_default_config_create_terminal_matches_shell_rule_sets():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    config = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))
    rules = config["permissions"]["rules"]

    tracked_patterns = {
        "dir *", "ls *", "pwd *", "cd *", "echo *", "cat *", "type *", "head *", "tail *",
        "git status *", "git log *", "git diff *", "git branch *",
        "rm *", "del *", "rd *", "mv *", "cp *", "chmod *", "chown *",
    }

    for rule in rules:
        pattern = rule.get("pattern")
        if pattern not in tracked_patterns:
            continue
        tools = set(rule.get("tools") or [])
        assert {"bash", "mcp_exec_command", "create_terminal"}.issubset(tools)


def test_default_config_asks_sensitive_shell_file_reads():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))["permissions"]

    perm, _ = evaluate_tiered_policy(permissions, "bash", {"command": "cat ~/.ssh/id_rsa"})
    assert perm == PermissionLevel.ASK


def test_default_config_asks_sensitive_file_tools():
    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))["permissions"]

    perm, _ = evaluate_tiered_policy(permissions, "read_file", {"path": "/workspace/.env"})
    assert perm == PermissionLevel.ASK


def test_builtin_rules_block_system_control_commands(tmp_path, monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.delenv("JIUWENCLAW_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tiered_policy_module, "_BUILTIN_RULES_CACHE", None)

    permissions = {
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "tools": {"bash": "allow"},
        "rules": [],
    }

    for command in ("shutdown now", "reboot", "halt", "poweroff", "init 0", "telinit 6"):
        perm, _ = evaluate_tiered_policy(permissions, "bash", {"command": command})
        assert perm == PermissionLevel.DENY


def test_default_config_asks_service_restart_and_process_kill_commands(tmp_path, monkeypatch):
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    monkeypatch.delenv("JIUWENCLAW_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tiered_policy_module, "_BUILTIN_RULES_CACHE", None)
    # Windows 上 Path.home() 常来自 USERPROFILE，与 HOME 无关；避免读到本机 ~/.jiuwenclaw 的扩展 builtin
    monkeypatch.setattr(
        tiered_policy_module,
        "_resolve_builtin_rules_yaml_path",
        tiered_policy_module.get_package_builtin_rules_path,
    )

    resources_dir = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "resources"
    permissions = yaml.safe_load((resources_dir / "config.yaml").read_text(encoding="utf-8"))["permissions"]

    for command in ("systemctl restart nginx", "service nginx restart", "pkill uvicorn", "kill -9 1234"):
        perm, _ = evaluate_tiered_policy(permissions, "bash", {"command": command})
        assert perm == PermissionLevel.ASK


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


def test_default_sensitive_rule_can_persist_always_allow(tmp_path, monkeypatch):
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
    overrides = saved["permissions"].get("approval_overrides") or []
    assert len(overrides) == 1
    assert overrides[0]["tools"] == ["bash"]
    assert overrides[0]["match_type"] == "command"
    assert overrides[0]["pattern"] == "cat ~/.ssh/id_rsa"
    assert overrides[0]["action"] == "allow"


def test_build_shell_permission_suggestions_prefers_exact_for_simple_command():
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status",
        shell_ast_result=ShellAstParseResult(
            kind="simple",
            subcommands=(ShellSubcommand(text="git status"),),
            flags=ShellStructureFlags(),
            backend="test",
        ),
    )

    assert len(suggestions) == 1
    assert suggestions[0].pattern == "git status"
    assert suggestions[0].scope == "exact"


def test_build_shell_permission_suggestions_returns_exact_rules_for_compound():
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status && npm test",
        shell_ast_result=ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="npm test"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )

    assert [item.pattern for item in suggestions] == ["git status", "npm test"]


def test_build_shell_permission_suggestions_dedupes_duplicate_subcommands():
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status && git status",
        shell_ast_result=ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="git status"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )

    assert [item.pattern for item in suggestions] == ["git status"]


def test_build_shell_permission_suggestions_uses_prefix_for_multiline_command():
    suggestions = build_shell_permission_suggestions(
        "bash",
        "python script.py\npython script.py --verbose",
        shell_ast_result=ShellAstParseResult(
            kind="simple",
            subcommands=(ShellSubcommand(text="python script.py\npython script.py --verbose"),),
            flags=ShellStructureFlags(),
            backend="test",
        ),
    )

    assert len(suggestions) == 1
    assert suggestions[0].pattern == "python script.py *"
    assert suggestions[0].scope == "prefix"


def test_build_shell_permission_suggestions_returns_empty_for_parse_unavailable_risky():
    suggestions = build_shell_permission_suggestions(
        "bash",
        "cat notes.txt > out.txt",
        shell_ast_result=ShellAstParseResult(
            kind="parse_unavailable",
            flags=ShellStructureFlags(has_output_redirection=True),
            reason="fallback detected shell structure",
            backend="test",
        ),
    )

    assert suggestions == []


def test_persist_permission_allow_rule_writes_multiple_overrides_for_compound_command(tmp_path, monkeypatch):
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
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")

    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    monkeypatch.setattr(
        tiered_policy_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="npm test"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )
    monkeypatch.setattr(
        shell_ast_module,
        "parse_shell_for_permission",
        lambda _command: ShellAstParseResult(
            kind="simple",
            subcommands=(
                ShellSubcommand(text="git status"),
                ShellSubcommand(text="npm test"),
            ),
            flags=ShellStructureFlags(has_compound_operators=True, has_actual_operator_nodes=True, operators=("&&",)),
            backend="test",
        ),
    )
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("bash", {"command": "git status && npm test"})

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    overrides = saved["permissions"].get("approval_overrides") or []
    assert [item["pattern"] for item in overrides] == ["git status", "npm test"]


def test_persist_permission_allow_rule_skips_complex_shell_suggestion(tmp_path, monkeypatch):
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
    tiered_policy_module = importlib.import_module("jiuwenclaw.agentserver.permissions.tiered_policy")
    shell_ast_module = importlib.import_module("jiuwenclaw.agentserver.permissions.shell_ast")

    monkeypatch.setattr(config_module, "_CONFIG_YAML_PATH", cfg_path)
    risky_result = ShellAstParseResult(
        kind="parse_unavailable",
        flags=ShellStructureFlags(has_output_redirection=True),
        reason="fallback detected shell structure",
        backend="test",
    )
    monkeypatch.setattr(tiered_policy_module, "parse_shell_for_permission", lambda _command: risky_result)
    monkeypatch.setattr(shell_ast_module, "parse_shell_for_permission", lambda _command: risky_result)
    set_permission_engine(PermissionEngine({"schema": "tiered_policy"}))

    persist_permission_allow_rule("bash", {"command": "cat notes.txt > out.txt"})

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


def test_interface_always_allow_payload_marks_persist_allow():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "interface.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    method_src = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "JiuWenClaw":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "_build_interactive_input_from_answers":
                    method_src = ast.get_source_segment(source, child)
                    break

    assert method_src is not None
    assert '"persist_allow": True' in method_src


def test_permission_rail_shell_auto_confirm_key_requires_simple_command():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "rails"
        / "permission_rail.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    helper_src = None
    getter_src = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PermissionInterruptRail":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "_build_shell_auto_confirm_key":
                    helper_src = ast.get_source_segment(source, child)
                if isinstance(child, ast.FunctionDef) and child.name == "_get_auto_confirm_key":
                    getter_src = ast.get_source_segment(source, child)

    assert getter_src is not None
    assert "return self._build_shell_auto_confirm_key(tool_name, str(cmd or \"\"))" in getter_src
    assert helper_src is not None
    assert 'if shell_ast_result.kind != "simple":' in helper_src
    assert "if shell_ast_result.flags.has_risky_structure():" in helper_src
    assert "if len(shell_ast_result.subcommands) != 1:" in helper_src
    assert 'return f"{tool_name}:{subcommand}"' in helper_src


def test_permission_rail_allow_always_persists_rule_without_session_auto_confirm(monkeypatch):
    del monkeypatch
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "rails"
        / "permission_rail.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    resolve_src = None
    parse_src = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PermissionInterruptRail":
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "resolve_interrupt":
                    resolve_src = ast.get_source_segment(source, child)
                if isinstance(child, ast.FunctionDef) and child.name == "_parse_confirm_payload":
                    parse_src = ast.get_source_segment(source, child)

    assert parse_src is not None
    assert 'persist_allow=bool(user_input.get("persist_allow", False))' in parse_src
    assert resolve_src is not None
    assert "persisted = persist_permission_allow_rule(normalized_name, tool_args)" in resolve_src
    assert "if self._should_store_auto_confirm(" in resolve_src


def test_permission_rail_allow_always_does_not_session_auto_confirm_compound_shell(monkeypatch):
    del monkeypatch
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "rails"
        / "permission_rail.py"
    )
    source = module_path.read_text(encoding="utf-8")
    assert "if shell_ast_result.kind != \"simple\":" in source
    assert "if shell_ast_result.flags.has_risky_structure():" in source
    assert "if len(shell_ast_result.subcommands) != 1:" in source
