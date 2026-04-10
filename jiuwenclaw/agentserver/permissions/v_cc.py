# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""v_cc 权限模型：整工具 + rules(severity) + defaults，命中项取最严."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.permissions.patterns import match_path, match_wildcard

logger = logging.getLogger(__name__)

_STRICT_ORDER = {PermissionLevel.DENY: 0, PermissionLevel.ASK: 1, PermissionLevel.ALLOW: 2}

# 规则内 tools 必须同类（与产品设计一致）
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command"})
_PATH_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
})
_NETWORK_TOOLS = frozenset({"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"})

_PATH_ARG_KEYS = frozenset({
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
})


def permissions_schema_is_v_cc(config: dict[str, Any]) -> bool:
    """``permissions.schema`` / ``permissions.version`` 为 v_cc（或兼容 v4.2）时启用."""
    raw = config.get("schema") or config.get("version")
    if not isinstance(raw, str):
        return False
    key = raw.strip().lower().replace(" ", "")
    return key in ("v_cc", "v4.2")


def _parse_level(value: str) -> PermissionLevel:
    v = (value or "").strip().lower()
    return PermissionLevel(v)


def strictest(*levels: PermissionLevel) -> PermissionLevel:
    if not levels:
        return PermissionLevel.ASK
    return min(levels, key=lambda p: _STRICT_ORDER[p])


def severity_to_decision(severity: str, permission_mode: str) -> PermissionLevel:
    sev = (severity or "").strip().upper()
    mode = (permission_mode or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"
    if sev == "LOW":
        return PermissionLevel.ALLOW
    if sev == "MEDIUM":
        return PermissionLevel.ASK if mode == "strict" else PermissionLevel.ALLOW
    if sev == "HIGH":
        return PermissionLevel.ASK
    if sev == "CRITICAL":
        return PermissionLevel.DENY if mode == "strict" else PermissionLevel.ASK
    logger.warning("[v_cc] unknown severity %r, treating as HIGH", severity)
    return PermissionLevel.ASK


def _tool_category(tool_name: str) -> str | None:
    if tool_name in _SHELL_TOOLS:
        return "shell"
    if tool_name in _PATH_TOOLS:
        return "path"
    if tool_name in _NETWORK_TOOLS:
        return "network"
    return None


def rule_tools_category_consistent(tools: list[str]) -> bool:
    cats: set[str] = set()
    for t in tools:
        c = _tool_category(t)
        if c is None:
            return False
        cats.add(c)
        if len(cats) > 1:
            return False
    return bool(cats)


def _command_text(tool_args: dict[str, Any]) -> str:
    return str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()


def _shell_pattern_matches(pattern: str, command: str) -> bool:
    if not pattern or not command:
        return False
    p = pattern.strip()
    if p.lower().startswith("re:"):
        expr = p[3:].strip()
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        try:
            return bool(re.search(expr, command, flags))
        except re.error:
            logger.warning("[v_cc] invalid shell regex %r", expr)
            return False
    glob_chars = frozenset("*?[")
    if any(ch in p for ch in glob_chars):
        return match_wildcard(command, p)
    return command == p


def _path_pattern_matches(pattern: str, value: str) -> bool:
    if not pattern or not value:
        return False
    p = pattern.strip()
    if p.lower().startswith("re:"):
        expr = p[3:].strip()
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        try:
            return bool(re.search(expr, value.replace("\\", "/"), flags))
        except re.error:
            logger.warning("[v_cc] invalid path regex %r", expr)
            return False
    return match_path(p, value)


def _tool_arg_value_looks_like_path(arg_key: str, value: str) -> bool:
    """是否把该参数值纳入路径类 pattern 匹配（已知名或形似路径）。"""
    if arg_key in _PATH_ARG_KEYS:
        return True
    if "/" in value or "\\" in value:
        return True
    return len(value) > 1 and value[1] == ":"


def _iter_path_strings(_tool_name: str, tool_args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k, v in tool_args.items():
        if not isinstance(v, str) or not v.strip():
            continue
        if _tool_arg_value_looks_like_path(k, v):
            out.append(v.strip())
    return out


def v_cc_rule_matches(
        tool_name: str,
        pattern: str,
        tool_args: dict[str, Any],
        rule_tools: list[str],
) -> bool:
    """单条 rule 是否对本次调用匹配（调用前已确认 tool_name in rule_tools）."""
    if not rule_tools:
        return False
    cat = _tool_category(rule_tools[0])
    if cat == "shell":
        return _shell_pattern_matches(pattern, _command_text(tool_args))
    if cat == "path":
        for val in _iter_path_strings(tool_name, tool_args):
            if _path_pattern_matches(pattern, val):
                return True
        return False
    if cat == "network":
        # 产品设计：网络类暂仅整工具；参数规则不匹配
        return False
    return False


def _baseline_level(tools_cfg: dict[str, Any], tool_name: str) -> tuple[PermissionLevel | None, str | None]:
    if tool_name not in tools_cfg:
        return None, None
    raw = tools_cfg[tool_name]
    if isinstance(raw, str):
        try:
            return _parse_level(raw), f"tools.{tool_name}"
        except ValueError:
            logger.warning("[v_cc] invalid tools.%s level %r", tool_name, raw)
            return None, None
    if isinstance(raw, dict) and isinstance(raw.get("*"), str):
        try:
            logger.warning(
                "[v_cc] tools.%s uses legacy dict; v_cc expects scalar — using '*' only",
                tool_name,
            )
            return _parse_level(raw["*"]), f"tools.{tool_name}.*"
        except ValueError:
            return None, None
    logger.warning("[v_cc] tools.%s is not a scalar allow|ask|deny; ignored for baseline", tool_name)
    return None, None


def evaluate_v_cc(
        permission_config: dict[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
) -> tuple[PermissionLevel, str]:
    """返回 (最终权限, matched_rule 摘要)."""
    mode = str(permission_config.get("permission_mode") or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"

    tools_cfg = permission_config.get("tools") or {}
    if not isinstance(tools_cfg, dict):
        tools_cfg = {}

    defaults_cfg = permission_config.get("defaults") or {}
    if not isinstance(defaults_cfg, dict):
        defaults_cfg = {}

    rules = permission_config.get("rules") or []
    if not isinstance(rules, list):
        rules = []

    candidates: list[tuple[PermissionLevel, str]] = []

    bl, bl_rule = _baseline_level(tools_cfg, tool_name)
    if bl is not None:
        candidates.append((bl, bl_rule))

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        r_tools = rule.get("tools") or []
        if isinstance(r_tools, str):
            r_tools = [r_tools]
        if not isinstance(r_tools, list) or tool_name not in r_tools:
            continue
        r_tools_s = [str(x).strip() for x in r_tools if isinstance(x, str) and str(x).strip()]
        if not rule_tools_category_consistent(r_tools_s):
            logger.warning(
                "[v_cc] skip rule %r: tools must be same category %s",
                rule.get("id"),
                r_tools_s,
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not v_cc_rule_matches(tool_name, pattern, tool_args, r_tools_s):
            continue
        sev = rule.get("severity", "HIGH")
        if not isinstance(sev, str):
            sev = "HIGH"
        dec = severity_to_decision(sev, mode)
        rid = rule.get("id", "")
        label = f"rules[{rid}]" if rid else "rules[?]"
        candidates.append((dec, label))

    if "*" in defaults_cfg and isinstance(defaults_cfg["*"], str):
        try:
            candidates.append((_parse_level(defaults_cfg["*"]), "defaults.*"))
        except ValueError:
            logger.warning("[v_cc] invalid defaults.* %r", defaults_cfg["*"])

    if not candidates:
        return PermissionLevel.ASK, "v_cc:fallback(no_config)"

    final = strictest(*(c[0] for c in candidates))
    contributing = sorted({r for lev, r in candidates if lev == final})
    matched = "v_cc:" + "+".join(contributing) if contributing else "v_cc"
    return final, matched


def maybe_escalate_shell_operators(
        tool_name: str,
        tool_args: dict[str, Any],
        permission: PermissionLevel,
) -> PermissionLevel:
    """与旧版一致：命令含链式/注入元字符时 ALLOW→ASK."""
    if tool_name not in ("mcp_exec_command", "bash"):
        return permission
    if permission != PermissionLevel.ALLOW:
        return permission
    from jiuwenclaw.agentserver.permissions.checker import _SHELL_OPERATORS_RE

    cmd = _command_text(tool_args)
    if cmd and _SHELL_OPERATORS_RE.search(cmd):
        return PermissionLevel.ASK
    return permission
