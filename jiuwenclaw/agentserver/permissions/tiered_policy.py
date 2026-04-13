# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""分层工具权限策略（tiered_policy）：内置参数规则 > 用户参数规则；整工具存在则不用默认。"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.permissions.patterns import match_path, match_wildcard

logger = logging.getLogger(__name__)

_STRICT_ORDER = {PermissionLevel.DENY: 0, PermissionLevel.ASK: 1, PermissionLevel.ALLOW: 2}

# ``permissions.schema`` / ``version`` 识别为分层策略（含旧别名）
_TIERED_POLICY_SCHEMA_KEYS = frozenset({"tiered_policy", "v_cc", "v4.2"})

# 规则内 tools 必须同类（与产品设计一致）
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
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

# (resolved_path_str, mtime, rules)；文件变更后 mtime 变化会重新加载
_BUILTIN_RULES_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None

_MR = "tiered_policy"
_APPROVAL_OVERRIDES_PREFIX = f"{_MR}:approval_overrides"


def _package_builtin_rules_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "resources" / "builtin_rules.yaml"


def get_package_builtin_rules_path() -> Path:
    """包内 ``resources/builtin_rules.yaml`` 的绝对路径。

    不经过用户配置目录；供测试或需固定使用发行版内置规则文件的场景调用。
    """
    return _package_builtin_rules_path()


def _resolve_builtin_rules_yaml_path() -> Path | None:
    """优先用户配置目录（与 ``config.yaml`` 同目录）下的 ``builtin_rules.yaml``，否则包内 resources。"""
    user_dir = os.getenv("JIUWENCLAW_CONFIG_DIR")
    if user_dir:
        user_path = Path(user_dir) / "builtin_rules.yaml"
        if user_path.is_file():
            return user_path
    fallback_user_path = Path.home() / ".jiuwenclaw" / "config" / "builtin_rules.yaml"
    if fallback_user_path.is_file():
        return fallback_user_path
    pkg_path = _package_builtin_rules_path()
    if pkg_path.is_file():
        return pkg_path
    logger.warning(
        "builtin_rules.yaml not found under %s or %s",
        fallback_user_path,
        pkg_path,
    )
    return None


def get_builtin_security_rules() -> list[dict[str, Any]]:
    """内置安全规则列表（进程内按路径+mtime 缓存）。

    加载顺序：``get_config_dir()/builtin_rules.yaml`` → 包内 ``resources/builtin_rules.yaml``。
    """
    global _BUILTIN_RULES_CACHE
    path = _resolve_builtin_rules_yaml_path()
    if path is None:
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(path.resolve())
    if _BUILTIN_RULES_CACHE is not None:
        ck, mt, rules = _BUILTIN_RULES_CACHE
        if ck == key and mt == mtime:
            return rules
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    _BUILTIN_RULES_CACHE = (key, mtime, rules)
    return rules


def collect_builtin_permission_rail_tool_names() -> list[str]:
    """内置规则中出现的工具名（供护栏合并）。"""
    names: set[str] = set()
    for rule in get_builtin_security_rules():
        raw_tools = rule.get("tools") or []
        if isinstance(raw_tools, str):
            raw_tools = [raw_tools]
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if isinstance(item, str) and item.strip():
                    names.add(item.strip())
    return sorted(names)


def permissions_schema_is_tiered_policy(config: dict[str, Any]) -> bool:
    """``permissions.schema`` / ``permissions.version`` 为 tiered_policy（或兼容 v_cc / v4.2）时启用."""
    raw = config.get("schema") or config.get("version")
    if not isinstance(raw, str):
        return False
    key = raw.strip().lower().replace(" ", "")
    return key in _TIERED_POLICY_SCHEMA_KEYS


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
    logger.warning("unknown severity %r, treating as HIGH", severity)
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
        norm = command.replace("\\", "/")

        def _try_subexpr(sub: str) -> bool:
            if not sub:
                return False
            try:
                if re.search(sub, command, flags):
                    return True
                if norm != command and re.search(sub, norm, flags):
                    return True
            except re.error:
                return False
            return False

        try:
            if re.search(expr, command, flags):
                return True
            if norm != command and re.search(expr, norm, flags):
                return True
        except re.error:
            # 例如 YAML 双引号落盘后 `C:\Users` 变成非法 \U；add_dir 旧版 `posix|win` 第二支整段编译失败
            if "|" in expr:
                for part in expr.split("|"):
                    if _try_subexpr(part.strip()):
                        return True
            logger.warning("invalid shell regex %r", expr)
            return False
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
            logger.warning("invalid path regex %r", expr)
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


def _collect_param_rule_hits(
        rules: list[dict[str, Any]],
        tool_name: str,
        tool_args: dict[str, Any],
        mode: str,
        label_ns: str,
) -> list[tuple[PermissionLevel, str]]:
    """参数级规则命中列表 (level, label)；``label_ns`` 为 ``builtin`` 或 ``rules``。"""
    hits: list[tuple[PermissionLevel, str]] = []
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
                "skip rule %r: tools must be same category %s",
                rule.get("id"),
                r_tools_s,
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not tiered_policy_rule_matches(tool_name, pattern, tool_args, r_tools_s):
            continue
        action = rule.get("action")
        if isinstance(action, str) and action.strip():
            dec = _parse_level(action)
        else:
            sev = rule.get("severity", "HIGH")
            if not isinstance(sev, str):
                sev = "HIGH"
            dec = severity_to_decision(sev, mode)
        rid = rule.get("id", "")
        label = f"{label_ns}[{rid}]" if rid else f"{label_ns}[?]"
        hits.append((dec, label))
    return hits


def _collect_approval_override_hits(
        rules: list[dict[str, Any]],
        tool_name: str,
        tool_args: dict[str, Any],
) -> list[str]:
    """用户审批后持久化的 allow override 命中列表。"""
    hits: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action") or "").strip().lower()
        if action != "allow":
            continue
        r_tools = rule.get("tools") or []
        if isinstance(r_tools, str):
            r_tools = [r_tools]
        if not isinstance(r_tools, list) or tool_name not in r_tools:
            continue
        r_tools_s = [str(x).strip() for x in r_tools if isinstance(x, str) and str(x).strip()]
        if not rule_tools_category_consistent(r_tools_s):
            logger.warning(
                "skip approval override %r: tools must be same category %s",
                rule.get("id"),
                r_tools_s,
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not tiered_policy_rule_matches(tool_name, pattern, tool_args, r_tools_s):
            continue
        rid = rule.get("id", "")
        label = f"approval_overrides[{rid}]" if rid else "approval_overrides[?]"
        hits.append(label)
    return hits


def tiered_policy_rule_matches(
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
            logger.warning("invalid tools.%s level %r", tool_name, raw)
            return None, None
    if isinstance(raw, dict) and isinstance(raw.get("*"), str):
        try:
            logger.warning(
                "tools.%s uses legacy dict; tiered_policy expects scalar — using '*' only",
                tool_name,
            )
            return _parse_level(raw["*"]), f"tools.{tool_name}.*"
        except ValueError:
            return None, None
    logger.warning("tools.%s is not a scalar allow|ask|deny; ignored for baseline", tool_name)
    return None, None


def _finalize_hits(hits: list[tuple[PermissionLevel, str]], prefix: str) -> tuple[PermissionLevel, str]:
    if any(lev == PermissionLevel.DENY for lev, _ in hits):
        contributing = sorted({r for lev, r in hits if lev == PermissionLevel.DENY})
        return PermissionLevel.DENY, f"{_MR}:{prefix}:deny:" + "+".join(contributing)
    final = strictest(*(h[0] for h in hits))
    contributing = sorted({r for lev, r in hits if lev == final})
    matched = f"{_MR}:{prefix}:" + "+".join(contributing) if contributing else f"{_MR}:{prefix}"
    return final, matched


def evaluate_tiered_policy(
        permission_config: dict[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
) -> tuple[PermissionLevel, str]:
    """返回 (最终权限, matched_rule 摘要).

    - 整工具 ``deny`` 优先于参数级放行。
    - 内置参数规则一旦命中则不再看用户 ``rules``。
    - 有参数级命中时结果仅来自该层（内置或用户）。
    - 无参数级命中时：仅有整工具则用整工具；否则仅用默认（整工具存在则忽略默认）。
    """
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
    approval_overrides = permission_config.get("approval_overrides") or []
    if not isinstance(approval_overrides, list):
        approval_overrides = []

    bl, bl_rule = _baseline_level(tools_cfg, tool_name)
    if bl == PermissionLevel.DENY:
        return PermissionLevel.DENY, bl_rule or f"{_MR}:tools.deny"

    builtin_hits = _collect_param_rule_hits(
        get_builtin_security_rules(), tool_name, tool_args, mode, "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    user_hits = _collect_param_rule_hits(rules, tool_name, tool_args, mode, "rules")
    if any(lev == PermissionLevel.DENY for lev, _ in user_hits):
        return _finalize_hits(user_hits, "rules")

    override_hits = _collect_approval_override_hits(
        approval_overrides, tool_name, tool_args,
    )
    if override_hits:
        contributing = sorted(set(override_hits))
        return PermissionLevel.ALLOW, _APPROVAL_OVERRIDES_PREFIX + ":" + "+".join(contributing)

    if builtin_hits:
        return _finalize_hits(builtin_hits, "builtin")

    if user_hits:
        return _finalize_hits(user_hits, "rules")

    if bl is not None:
        return bl, bl_rule or f"{_MR}:tools"

    if "*" in defaults_cfg and isinstance(defaults_cfg["*"], str):
        try:
            dl = _parse_level(defaults_cfg["*"])
            return dl, f"{_MR}:defaults.*"
        except ValueError:
            logger.warning("invalid defaults.* %r", defaults_cfg["*"])

    return PermissionLevel.ASK, f"{_MR}:fallback(no_config)"


def maybe_escalate_shell_operators(
        tool_name: str,
        tool_args: dict[str, Any],
        permission: PermissionLevel,
) -> PermissionLevel:
    """与旧版一致：命令含链式/注入元字符时 ALLOW→ASK."""
    if tool_name not in ("mcp_exec_command", "bash", "create_terminal"):
        return permission
    if permission != PermissionLevel.ALLOW:
        return permission
    from jiuwenclaw.agentserver.permissions.checker import _SHELL_OPERATORS_RE

    cmd = _command_text(tool_args)
    if cmd and _SHELL_OPERATORS_RE.search(cmd):
        return PermissionLevel.ASK
    return permission


def matched_rule_uses_approval_override(matched_rule: str | None) -> bool:
    """当前结果是否来自 approval_overrides。"""
    if not isinstance(matched_rule, str):
        return False
    return matched_rule.startswith(_APPROVAL_OVERRIDES_PREFIX)
