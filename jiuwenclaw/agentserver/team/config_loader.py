# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 配置加载器.

从 config.yaml 的 team 部分加载配置，构建 TeamAgentSpec 需要的字典格式.
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenclaw.config import get_config
from jiuwenclaw.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)


def load_team_spec_dict(session_id: str) -> dict[str, Any]:
    """加载 Team 配置并构建 TeamAgentSpec 需要的字典格式.

    Args:
        session_id: 会话 ID，用于生成唯一的 team_name

    Returns:
        可直接传给 TeamAgentSpec.model_validate() 的字典
    """
    config_base = get_config()
    team_raw = config_base.get("team", {})

    if not team_raw:
        logger.warning("[TeamConfigLoader] 配置文件中没有 team 配置，使用默认配置")
        team_raw = {}

    model_config = config_base.get("models", {}).get("default", {})
    model_client_config = model_config.get("model_client_config", {})
    model_request_config = dict(model_config.get("model_config_obj", {}))

    model_name = model_client_config.get("model_name", "")
    if model_name and "model" not in model_request_config:
        model_request_config["model"] = model_name

    logger.info(
        "[TeamConfigLoader] model config loaded: model_name=%s, provider=%s",
        model_name,
        model_client_config.get("client_provider", "unknown"),
    )

    model_dict = {
        "model_client_config": model_client_config,
        "model_request_config": model_request_config,
    }

    workspace_config = team_raw.get("workspace", {})
    stable_base = workspace_config.get("stable_base", True)
    max_iterations = workspace_config.get("max_iterations", 200)
    completion_timeout = workspace_config.get("completion_timeout", 600.0)

    workspace_dict = {"stable_base": stable_base}

    agents_raw = team_raw.get("agents", {})
    if not agents_raw:
        agents_raw = {"leader": {}, "teammate": {}}
        logger.warning("[TeamConfigLoader] agents 配置为空，使用默认 leader/teammate")

    agents = {}
    for agent_name, agent_config in agents_raw.items():
        agent_dict = {
            "model": model_dict,
            "workspace": workspace_dict,
            "max_iterations": max_iterations,
            "completion_timeout": completion_timeout,
        }
        if agent_config.get("member_name"):
            agent_dict["member_name"] = agent_config["member_name"]
        if agent_config.get("name"):
            agent_dict["name"] = agent_config["name"]
        agents[agent_name] = agent_dict

    spec_dict = {
        "team_name": f"{team_raw.get('team_name', 'team')}_{session_id}",
        "lifecycle": team_raw.get("lifecycle", "persistent"),
        "teammate_mode": team_raw.get("teammate_mode", "build_mode"),
        "spawn_mode": team_raw.get("spawn_mode", "inprocess"),
        "agents": agents,
    }

    transport_raw = team_raw.get("transport", {})
    if transport_raw:
        spec_dict["transport"] = transport_raw

    storage_raw = team_raw.get("storage", {})
    if storage_raw:
        storage_params = storage_raw.get("params", {})
        if "connection_string" in storage_params:
            from pathlib import Path
            conn_str = storage_params["connection_string"]
            db_path = Path(conn_str)
            if not db_path.is_absolute():
                workspace_dir = get_agent_workspace_dir()
                db_path = workspace_dir / "team_data" / conn_str
                storage_params["connection_string"] = str(db_path)
            db_dir = db_path.parent
            if not db_dir.exists():
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info("[TeamConfigLoader] Created database directory: %s", db_dir)
        spec_dict["storage"] = storage_raw

    logger.info(
        "[TeamConfigLoader] 配置加载成功: team_name=%s, lifecycle=%s, agents=%s",
        spec_dict["team_name"],
        spec_dict["lifecycle"],
        list(agents.keys()),
    )

    return spec_dict
