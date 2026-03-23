# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from jiuwenclaw.utils import get_config_file


_CONFIG_MODULE_DIR = Path(__file__).parent
_CONFIG_YAML_PATH = get_config_file()

# Check if user workspace exists and use it if configured via env
_user_config = os.getenv("JIUWENCLAW_CONFIG_DIR")
if _user_config:
    _CONFIG_MODULE_DIR = Path(_user_config)
elif (Path.home() / ".jiuwenclaw" / "config").exists():
    _CONFIG_MODULE_DIR = Path.home() / ".jiuwenclaw" / "config"

# Ensure config directory is in sys.path
if str(_CONFIG_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_MODULE_DIR))


def resolve_env_vars(value: Any) -> Any:
    """递归解析配置中的环境变量替换语法 ${VAR:-default}.

    Args:
        value: 配置值，可能是字符串、字典或列表

    Returns:
        解析后的值
    """
    if isinstance(value, str):
        # 匹配 ${VAR:-default} 格式
        pattern = r'\$\{([^:}]+)(?::-([^}]*))?\}'

        def replace_env(match):
            var_name = match.group(1)
            default = match.group(2)
            current = os.getenv(var_name)
            # Bash: ${VAR:-default} uses default when VAR is unset OR empty.
            # ${VAR} (no :-) keeps getenv behavior; unset -> "".
            if default is not None:
                if current is None or current == "":
                    return default
                return current
            return current if current is not None else ""

        return re.sub(pattern, replace_env, value)
    elif isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    else:
        return value


def get_config():
    with open(get_config_file(), "r", encoding="utf-8") as f:
        config_base = yaml.safe_load(f)
    config_base = resolve_env_vars(config_base)

    return config_base


def get_config_raw():
    """读 config.yaml 原始内容（不解析环境变量），供局部更新后写回。"""
    with open(_CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def set_config(config):
    with open(_CONFIG_YAML_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def _load_yaml_round_trip(config_path: Path):
    """ruamel 加载 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    with open(config_path, "r", encoding="utf-8") as f:
        return rt.load(f)


def _dump_yaml_round_trip(config_path: Path, data: Any) -> None:
    """ruamel 写回 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    rt.default_flow_style = False
    # mapping 2 空格；list 用 sequence=4 + offset=2 保证 dash 前有 2 空格（tools: 下 - todo），否则 list 会变成无缩进
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.width = 4096
    with open(config_path, "w", encoding="utf-8") as f:
        rt.dump(data, f)


def update_heartbeat_in_config(payload: dict[str, Any]) -> None:
    """只更新 heartbeat 段并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "heartbeat" not in data:
        data["heartbeat"] = {}
    hb = data["heartbeat"]
    if "every" in payload:
        hb["every"] = payload["every"]
    if "target" in payload:
        hb["target"] = payload["target"]
    if "active_hours" in payload:
        hb["active_hours"] = payload["active_hours"]
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_channel_in_config(channel_id: str, conf: dict[str, Any]) -> None:
    """只更新 channels[channel_id] 并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    if channel_id not in channels:
        channels[channel_id] = {}
    section = channels[channel_id]
    for k, v in conf.items():
        section[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_preferred_language_in_config(lang: str) -> None:
    """只更新顶层 preferred_language 并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    data["preferred_language"] = lang
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def set_preferred_language_in_config_file(config_path: Path, lang: str) -> None:
    """将 preferred_language 写入指定 config.yaml（用于 init 等尚未绑定全局路径的场景）。"""
    lang = str(lang or "zh").strip().lower()
    if lang not in ("zh", "en"):
        lang = "zh"
    if not config_path.exists():
        return
    data = _load_yaml_round_trip(config_path)
    data["preferred_language"] = lang
    _dump_yaml_round_trip(config_path, data)


def update_browser_in_config(updates: dict[str, Any]) -> None:
    """只更新 browser 段（如 chrome_path）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "browser" not in data:
        data["browser"] = {}
    section = data["browser"]
    for k, v in updates.items():
        section[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_context_engine_enabled_in_config(value: bool) -> None:
    """更新 react.context_engine_config.enabled（上下文压缩开关）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "react" not in data:
        data["react"] = {}
    react = data["react"]
    if "context_engine_config" not in react:
        react["context_engine_config"] = {}
    react["context_engine_config"]["enabled"] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_permissions_enabled_in_config(value: bool) -> None:
    """更新 permissions.enabled（工具安全护栏开关）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["enabled"] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def merge_config_from_old(
    old_config_path: Path,
    new_config_path: Path,
    template_config_path: Path | None = None,
) -> None:
    """将旧版 config.yaml 合并到新版：以 template（或当前 new）为基准，旧值覆盖。旧 react.model_client_config -> models.default。"""
    if not old_config_path.exists():
        return
    # 以 resources/config.yaml 模板为基准，确保新结构完整
    template = Path(template_config_path) if template_config_path else None
    if template and template.exists():
        new_data = _load_yaml_round_trip(template)
    elif new_config_path.exists():
        new_data = _load_yaml_round_trip(new_config_path)
    else:
        return

    old_data = yaml.safe_load(old_config_path.read_text(encoding="utf-8")) or {}

    # 旧 react.model_client_config -> models.default
    old_react = old_data.get("react") or {}
    old_mcc = old_react.get("model_client_config")
    if old_mcc and isinstance(old_mcc, dict):
        if "models" not in new_data:
            new_data["models"] = {}
        if "default" not in new_data["models"]:
            new_data["models"]["default"] = {"model_client_config": {}, "model_config_obj": {}}
        default_cfg = new_data["models"]["default"]
        mcc = default_cfg.get("model_client_config") or {}
        for k, v in old_mcc.items():
            if v not in (None, ""):
                mcc[k] = v
        if "timeout" not in mcc:
            mcc["timeout"] = 1800
        if "verify_ssl" not in mcc:
            mcc["verify_ssl"] = False
        default_cfg["model_client_config"] = mcc

    # 合并 react 其他字段（agent_name, model_name 等），用 _deep_merge 保留新模板结构
    if "react" not in new_data:
        new_data["react"] = {}
    react = new_data["react"]
    for k, v in old_react.items():
        if k == "model_client_config" or v in (None, ""):
            continue
        if isinstance(v, dict) and isinstance(react.get(k), dict):
            _deep_merge(react[k], v)
        else:
            react[k] = v
    if "evolution" not in react:
        react["evolution"] = {}
    react["evolution"]["skill_base_dir"] = "agent/skills"

    # 合并其他段：preferred_language, heartbeat, channels, embed, email_settings, browser
    for section in ("preferred_language", "heartbeat", "channels", "embed", "email_settings", "browser"):
        old_val = old_data.get(section)
        if old_val is None:
            continue
        if section not in new_data:
            new_data[section] = old_val
        elif isinstance(old_val, dict) and isinstance(new_data.get(section), dict):
            _deep_merge(new_data[section], old_val)
        else:
            new_data[section] = old_val

    new_config_path.parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml_round_trip(new_config_path, new_data)


def _deep_merge(base: dict, overlay: dict) -> None:
    """将 overlay 的非空值合并到 base（就地修改 base）。"""
    for k, v in overlay.items():
        if v is None or v == "":
            continue
        if k not in base:
            base[k] = v
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def update_env_file(old_path=".env_bak", new_path=".env"):
    """将旧 .env 中的非空值合并到新 .env，保留新文件结构，旧 key 可补充到末尾。"""
    old_path = Path(old_path) if not isinstance(old_path, Path) else old_path
    new_path = Path(new_path) if not isinstance(new_path, Path) else new_path
    if not old_path.exists():
        return

    def _parse_value(v: str) -> str:
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return v[1:-1]
        return v

    old_env_dict: dict[str, str] = {}
    with open(old_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                val = _parse_value(v)
                if val:
                    old_env_dict[key] = val

    if not old_env_dict:
        return

    new_keys: set[str] = set()
    updated_lines: list[str] = []
    with open(new_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                new_keys.add(key)
                if key in old_env_dict:
                    # 保持新模板的 key 格式（是否带引号），值取旧的
                    updated_lines.append(f"{key}={old_env_dict[key]}\n")
                    continue
            updated_lines.append(line)

    # 补充旧文件中有值但新文件中没有的 key
    for k in old_env_dict:
        if k not in new_keys:
            updated_lines.append(f"{k}={old_env_dict[k]}\n")

    with open(new_path, "w", encoding="utf-8") as f:
        f.writelines(updated_lines)