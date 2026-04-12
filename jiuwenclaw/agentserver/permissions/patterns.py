# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""模式匹配器 - 仅支持 wildcard 模式；含权限规则持久化.

wildcard 模式：
- * → .*  (零个或多个)
- ? → .   (恰好一个)
- 正则元字符转义
- " *" 结尾 → ( .*)? 便于 "ls *" 匹配 "ls" 或 "ls -la"
- 全串锚定 ^...$ 防注入
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SHELL_APPROVAL_TOOLS = frozenset({"bash", "mcp_exec_command"})
_PATH_APPROVAL_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
})
_PATH_APPROVAL_KEYS = (
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
)


@dataclass(frozen=True)
class _ApprovalOverrideSignature:
    tool_name: str
    tools: list[str]
    match_type: str
    existing_match_type: str | None
    pattern: str
    existing_pattern: str | None
    existing_action: str


# 限制性字符类：仅允许命令参数和路径常见字符，排除 ; | & ` < > $ 等 shell 元字符防注入
# - 置于开头避免被解析为范围
_WILDCARD_CHARS = r'[-a-zA-Z0-9 \._/:"\']'


def match_wildcard(value: str, pattern: str) -> bool:
    """通配符匹配.

    - * → 限制性字符类* (排除 shell 元字符，防命令拼接)
    - ? → 限制性字符类 (恰好一个)
    - 正则元字符转义
    - " *" 结尾 → ( 字符类*)? 使 "ls *" 可匹配 "ls" 或 "ls -la"
    - 全串锚定 ^...$ 防止 "git status; rm -rf /" 匹配 "git status *"

    Args:
        value: 被匹配字符串（来自工具输入）
        pattern: 通配符模式（来自配置，可信）

    Returns:
        是否匹配
    """
    if not pattern or not value:
        return False
    val = value.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    # 1. 转义正则特殊字符（* 和 ? 保留，后续单独处理）
    to_escape = set(".+^${}()|[]\\")
    escaped = "".join("\\" + c if c in to_escape else c for c in pat)
    # 2. 先替换 ?（必须在 * 之前，否则会误替换 ")? " 中的 ?）
    escaped = escaped.replace("?", _WILDCARD_CHARS)
    # 3. * → 限制性字符类*
    if escaped.endswith(" *"):
        escaped = escaped[:-2] + "( " + _WILDCARD_CHARS + "*)?"
    else:
        escaped = escaped.replace("*", _WILDCARD_CHARS + "*")
    # 3. 全串锚定
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    try:
        return bool(re.match("^" + escaped + "$", val, flags))
    except re.error:
        return False




class PatternMatcher:
    """模式匹配器 - 仅支持 wildcard 模式 (*, ?)."""

    @staticmethod
    def match(pattern: str, value: str) -> bool:
        if not pattern or not value:
            return False
        return match_wildcard(value, pattern)

    def match_any(self, patterns: list[str], value: str) -> bool:
        """匹配任意一个模式."""
        return any(self.match(p, value) for p in patterns)


class PathMatcher:
    """路径匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_path(self, pattern: str, path: str | Path) -> bool:
        """匹配文件路径 (规范化分隔符后再比较)."""
        normalized_path = str(path).replace("\\", "/")
        normalized_pattern = pattern.replace("\\", "/")

        if self._pm.match(normalized_pattern, normalized_path):
            return True

        # 尝试匹配父目录层级
        path_obj = Path(str(path))
        for parent in path_obj.parents:
            parent_str = str(parent).replace("\\", "/")
            if self._pm.match(normalized_pattern, parent_str):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/"):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/*"):
                return True
        return False

    def match_path_any(self, patterns: list[str], path: str | Path) -> bool:
        return any(self.match_path(p, path) for p in patterns)


class URLMatcher:
    """URL 匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_url(self, pattern: str, url: str) -> bool:
        """匹配 URL (支持 hostname、netloc、full URL)."""
        if not url:
            return False
        if self._pm.match(pattern, url):
            return True
        try:
            parsed = urlparse(url)
            if self._pm.match(pattern, parsed.hostname or ""):
                return True
            if self._pm.match(pattern, parsed.netloc):
                return True
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if self._pm.match(pattern, base_url):
                return True
            if self._pm.match(pattern, base_url + "/*"):
                return True
        except Exception:
            return False
        return False

    def match_url_any(self, patterns: list[str], url: str) -> bool:
        return any(self.match_url(p, url) for p in patterns)


class CommandMatcher:
    """命令匹配器 - 仅支持 wildcard，全串锚定防注入."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_command(self, pattern: str, command: str) -> bool:
        """匹配命令字符串 (wildcard 模式，全串锚定)."""
        if not command:
            return False
        return self._pm.match(pattern, command)

    def match_command_any(self, patterns: list[str], command: str) -> bool:
        return any(self.match_command(p, command) for p in patterns)


# ----- 全局便捷函数 -----
_pattern_matcher = PatternMatcher()
_path_matcher = PathMatcher()
_url_matcher = URLMatcher()
_command_matcher = CommandMatcher()


def match_pattern(pattern: str, value: str) -> bool:
    return _pattern_matcher.match(pattern, value)


def match_path(pattern: str, path: str | Path) -> bool:
    return _path_matcher.match_path(pattern, path)


def match_url(pattern: str, url: str) -> bool:
    return _url_matcher.match_url(pattern, url)


def match_command(pattern: str, command: str) -> bool:
    return _command_matcher.match_command(pattern, command)


def build_command_allow_pattern(cmd: str) -> str:
    """构建匹配完整命令的通配符模式.

    Examples:
        "start chrome"   → start chrome *
        "npm install"    → npm install *
        "ls"             → ls *
    """
    return cmd.strip() + " *"


def contains_path(parent: str | Path, child: str | Path) -> bool:
    """子路径是否在父路径下（含路径穿越防护）.
    """
    import os
    try:
        rel = os.path.relpath(Path(child).resolve(), Path(parent).resolve())
        return not rel.startswith("..") and rel != ".."
    except (ValueError, OSError):
        return False


# ---------- 权限规则持久化 ----------


def persist_permission_allow_rule(tool_name: str, tool_args: dict | str) -> None:
    """用户选择「总是允许」时，将 allow 规则写入 config.yaml.

    For mcp_exec_command with a command arg, adds a wildcard pattern.
    For other tools, sets the tool to 'allow'.
    """
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception:
            tool_args = {}

    logger.info(
        "[Persist] START tool_name=%s tool_args_type=%s tool_args=%s",
        tool_name, type(tool_args).__name__, str(tool_args)[:200],
    )

    try:
        from jiuwenclaw.agentserver.permissions.core import get_permission_engine
        from jiuwenclaw.agentserver.permissions.tiered_policy import (
            evaluate_tiered_policy,
            permissions_schema_is_tiered_policy,
        )
        from jiuwenclaw.agentserver.permissions.models import PermissionLevel
        from jiuwenclaw.config import (
            _CONFIG_YAML_PATH,
            _load_yaml_round_trip,
            _dump_yaml_round_trip,
        )

        logger.info("[Persist] Config path: %s", _CONFIG_YAML_PATH)
        data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        permissions = data.get("permissions")
        if permissions is None:
            logger.warning("[Persist] ABORT: No 'permissions' section in config")
            return
        if permissions_schema_is_tiered_policy(permissions):
            current_permission, _matched_rule = evaluate_tiered_policy(
                permissions, tool_name, tool_args,
            )
            if current_permission != PermissionLevel.ASK:
                logger.warning(
                    "[Persist] Skip tiered approval override for %s because current permission is %s",
                    tool_name,
                    current_permission.value,
                )
            elif _persist_tiered_approval_override(permissions, tool_name, tool_args):
                logger.info("[Persist] Tiered approval override written for %s", tool_name)
            else:
                logger.warning(
                    "[Persist] Skip tiered approval override for %s because exact override could not be derived",
                    tool_name,
                )
        else:
            _persist_legacy_allow_rule(permissions, tool_name, tool_args)

        _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
        logger.info("[Persist] YAML written to disk")

        verify_data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        engine = get_permission_engine()
        engine.update_config(verify_data.get("permissions", {}))
        logger.info("[Persist] Engine hot-reloaded")

    except Exception:
        logger.error("[Persist] FAILED to persist permission allow rule", exc_info=True)


def _persist_legacy_allow_rule(permissions: dict, tool_name: str, tool_args: dict) -> None:
    tools_section = permissions.get("tools")
    if tools_section is None:
        permissions["tools"] = {}
        tools_section = permissions["tools"]

    if tool_name == "mcp_exec_command":
        cmd = str(tool_args.get("command", tool_args.get("cmd", "")))
        logger.info("[Persist] Extracted command: '%s'", cmd)
        if cmd:
            new_pattern = build_command_allow_pattern(cmd)
            logger.info("[Persist] Built legacy pattern: %s", new_pattern)

            tool_entry = tools_section.get("mcp_exec_command")
            if not isinstance(tool_entry, dict):
                tools_section["mcp_exec_command"] = {"*": "ask", "patterns": {}}
                tool_entry = tools_section["mcp_exec_command"]

            patterns = tool_entry.get("patterns")
            if patterns is None:
                tool_entry["patterns"] = {}
                patterns = tool_entry["patterns"]

            if isinstance(patterns, dict):
                if new_pattern in patterns:
                    logger.info("[Persist] Legacy pattern already exists, skip")
                    return
                patterns[new_pattern] = "allow"
            else:
                for p in patterns:
                    if isinstance(p, dict) and p.get("pattern") == new_pattern:
                        logger.info("[Persist] Legacy pattern already exists, skip")
                        return
                patterns.append({"pattern": new_pattern, "permission": "allow"})
            logger.info("[Persist] Appended legacy pattern: %s", new_pattern)
            return

    tools_section[tool_name] = "allow"
    logger.info("[Persist] Set %s = allow", tool_name)


def _persist_tiered_approval_override(
    permissions: dict,
    tool_name: str,
    tool_args: dict,
) -> bool:
    override_match = _build_tiered_approval_override_match(tool_name, tool_args)
    if override_match is None:
        return False
    match_type, pattern = override_match
    overrides = permissions.get("approval_overrides")
    if not isinstance(overrides, list):
        overrides = []
        permissions["approval_overrides"] = overrides

    for existing in overrides:
        if not isinstance(existing, dict):
            continue
        tools = existing.get("tools") or []
        if isinstance(tools, str):
            tools = [tools]
        existing_match_type = existing.get("match_type")
        existing_pattern = existing.get("pattern")
        existing_action = str(existing.get("action") or "").strip().lower()
        signature = _ApprovalOverrideSignature(
            tool_name=tool_name,
            tools=tools,
            match_type=match_type,
            existing_match_type=existing_match_type,
            pattern=pattern,
            existing_pattern=existing_pattern,
            existing_action=existing_action,
        )
        if _is_same_allow_override(signature):
            logger.info("[Persist] approval_override already exists, skip")
            return True

    overrides.append({
        "id": _build_approval_override_id(tool_name, match_type, pattern),
        "tools": [tool_name],
        "match_type": match_type,
        "pattern": pattern,
        "action": "allow",
        "source": "user_approval",
    })
    return True


def _build_tiered_approval_override_match(
    tool_name: str,
    tool_args: dict,
) -> tuple[str, str] | None:
    if tool_name in _SHELL_APPROVAL_TOOLS:
        command = str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()
        if command:
            return "command", command
        return None
    if tool_name in _PATH_APPROVAL_TOOLS:
        for key in _PATH_APPROVAL_KEYS:
            value = tool_args.get(key)
            if isinstance(value, str) and value.strip():
                return "path", value.strip()
        for key, value in tool_args.items():
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text:
                continue
            if _value_looks_like_path(key, text):
                return "path", text
    return None


def _value_looks_like_path(key: str, text: str) -> bool:
    if key in _PATH_APPROVAL_KEYS:
        return True
    if "/" in text or "\\" in text:
        return True
    return len(text) > 1 and text[1] == ":"


def _is_same_allow_override(signature: _ApprovalOverrideSignature) -> bool:
    if signature.tool_name not in signature.tools:
        return False
    if signature.existing_match_type != signature.match_type:
        return False
    if signature.existing_pattern != signature.pattern:
        return False
    return signature.existing_action == "allow"


def _build_approval_override_id(tool_name: str, match_type: str, pattern: str) -> str:
    raw = f"user_allow_{tool_name}_{match_type}_{pattern}"
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    if not collapsed:
        return "user_allow_override"
    return collapsed[:120]


def persist_external_directory_allow(paths: list[str]) -> None:
    """用户选择「总是允许」外部路径时，写入 external_directory 配置."""
    if not paths:
        return
    logger.info("[Persist] external_directory allow: paths=%s", paths[:3])
    try:
        from jiuwenclaw.agentserver.permissions.core import get_permission_engine
        from jiuwenclaw.config import (
            _CONFIG_YAML_PATH,
            _load_yaml_round_trip,
            _dump_yaml_round_trip,
        )
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        permissions = data.get("permissions")
        if permissions is None:
            permissions = {}
            data["permissions"] = permissions
        ext_cfg = permissions.get("external_directory")
        if not isinstance(ext_cfg, dict):
            ext_cfg = {"*": "ask"}
            permissions["external_directory"] = ext_cfg
        for path_str in paths:
            path_norm = path_str.replace("\\", "/").rstrip("/")
            parent = str(Path(path_norm).parent).replace("\\", "/")
            key = parent if parent and parent != "." else path_norm
            if key not in ext_cfg or ext_cfg[key] != "allow":
                ext_cfg[DoubleQuotedScalarString(key)] = DoubleQuotedScalarString("allow")
                logger.info("[Persist] Added external_directory[%s] = allow", key)
        _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
        engine = get_permission_engine()
        engine.update_config(data.get("permissions", {}))
        logger.info("[Persist] external_directory written, engine hot-reloaded")
    except Exception:
        logger.error("[Persist] FAILED to persist external_directory allow", exc_info=True)


def persist_cli_trusted_directory(raw_path: str) -> dict[str, Any]:
    """CLI ``command.add_dir``：全局信任目录子树。

    写入 ``permissions.external_directory``（以目录路径为前缀键），并在 ``tiered_policy`` 下追加
    ``approval_overrides``（路径类工具一条、shell 类工具一条），以便同时消除外部路径维度的 ASK
    与参数级 ASK。

    ``remember`` 由调用方忽略；本函数始终落盘。
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"ok": False, "error": "path is empty"}

    try:
        resolved = Path(raw_path.strip()).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return {"ok": False, "error": f"invalid path: {e}"}

    dir_norm = resolved.as_posix().rstrip("/")
    if not dir_norm:
        return {"ok": False, "error": "path resolves to empty"}

    try:
        from jiuwenclaw.agentserver.permissions.core import get_permission_engine
        from jiuwenclaw.agentserver.permissions.tiered_policy import (
            _PATH_TOOLS,
            _SHELL_TOOLS,
            permissions_schema_is_tiered_policy,
        )
        from jiuwenclaw.config import (
            _CONFIG_YAML_PATH,
            _load_yaml_round_trip,
            _dump_yaml_round_trip,
        )
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        permissions = data.get("permissions")
        if permissions is None:
            permissions = {}
            data["permissions"] = permissions

        ext_cfg = permissions.get("external_directory")
        if not isinstance(ext_cfg, dict):
            ext_cfg = {"*": "ask"}
            permissions["external_directory"] = ext_cfg
        ext_cfg[DoubleQuotedScalarString(dir_norm)] = DoubleQuotedScalarString("allow")
        logger.info("[Persist] cli add_dir external_directory[%s] = allow", dir_norm)

        path_pattern = "re:^" + re.escape(dir_norm) + r"(?:$|/)"
        posix = dir_norm
        win = posix.replace("/", "\\")
        shell_pattern = "re:" + rf".*{re.escape(posix)}.*|.*{re.escape(win)}.*"

        tiered = permissions_schema_is_tiered_policy(permissions)
        suffix = hashlib.sha256(dir_norm.encode("utf-8")).hexdigest()[:16]
        path_override_id = f"cli_trusted_path_{suffix}"
        shell_override_id = f"cli_trusted_shell_{suffix}"

        if tiered:
            overrides = permissions.get("approval_overrides")
            if not isinstance(overrides, list):
                overrides = []
                permissions["approval_overrides"] = overrides

            def _has_id(oid: str) -> bool:
                for r in overrides:
                    if isinstance(r, dict) and r.get("id") == oid:
                        return True
                return False

            path_tools = sorted(_PATH_TOOLS)
            if not _has_id(path_override_id):
                overrides.append({
                    "id": path_override_id,
                    "tools": path_tools,
                    "match_type": "path",
                    "pattern": path_pattern,
                    "action": "allow",
                    "source": "cli_add_dir",
                })
                logger.info("[Persist] cli add_dir approval_overrides path id=%s", path_override_id)

            shell_tools = sorted(_SHELL_TOOLS)
            if not _has_id(shell_override_id):
                overrides.append({
                    "id": shell_override_id,
                    "tools": shell_tools,
                    "match_type": "command",
                    "pattern": shell_pattern,
                    "action": "allow",
                    "source": "cli_add_dir",
                })
                logger.info("[Persist] cli add_dir approval_overrides shell id=%s", shell_override_id)
        else:
            logger.warning(
                "[Persist] cli add_dir: permissions not tiered_policy; only external_directory updated",
            )

        _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
        engine = get_permission_engine()
        engine.update_config(data.get("permissions", {}))
        return {
            "ok": True,
            "normalized": dir_norm,
            "path_pattern": path_pattern,
            "shell_pattern": shell_pattern,
            "tiered_overrides": tiered,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("[Persist] cli add_dir failed: %s", e)
        return {"ok": False, "error": str(e)}
