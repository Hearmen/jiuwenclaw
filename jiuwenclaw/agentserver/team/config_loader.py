# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 配置加载器.

从 YAML 配置文件加载 Team 配置，支持环境变量替换.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from jiuwenclaw.utils import get_agent_team_data_dir, get_config_file

logger = logging.getLogger(__name__)


@dataclass
class LeaderConfig:
    """Leader 配置."""
    member_name: str = "team_leader"
    name: str = "TeamLeader"
    persona: str = "天才项目管理专家"
    domain: str = "project_management"


@dataclass
class TeamMemberConfig:
    """团队成员配置."""
    member_name: str
    name: str
    role_type: str = "teammate"
    persona: str = ""
    domain: str = ""


@dataclass
class TransportConfig:
    """传输层配置."""
    type: str = "team_runtime"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StorageConfig:
    """存储层配置."""
    type: str = "sqlite"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamConfig:
    """Team 完整配置."""
    team_name: str = "jiuwen_team"
    lifecycle: str = "persistent"
    teammate_mode: str = "build_mode"
    spawn_mode: str = "coroutine"
    leader: LeaderConfig = field(default_factory=LeaderConfig)
    predefined_members: list[TeamMemberConfig] = field(default_factory=list)
    transport: TransportConfig = field(default_factory=TransportConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    metadata: dict[str, Any] = field(default_factory=dict)


class TeamConfigLoader:
    """Team 配置加载器."""

    _ENV_VAR_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")

    def __init__(self, config_path: Path | str | None = None):
        """初始化加载器.

        Args:
            config_path: 配置文件路径，默认使用 get_config_file() 获取
        """
        self.config_path = Path(config_path) if config_path else get_config_file()

    def _expand_env_vars(self, value: Any) -> Any:
        """递归替换环境变量.

        支持格式:
        - ${VAR}: 使用环境变量值，未设置则使用空字符串
        - ${VAR:-default}: 使用环境变量值，未设置则使用默认值
        """
        if isinstance(value, str):
            def replace_env(match: re.Match) -> str:
                var_name = match.group(1)
                default = match.group(2)
                current = os.getenv(var_name)
                if default is not None:
                    return current if current else default
                return current if current is not None else ""
            return self._ENV_VAR_RE.sub(replace_env, value)
        elif isinstance(value, dict):
            return {k: self._expand_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._expand_env_vars(item) for item in value]
        return value

    def load(self) -> TeamConfig:
        """加载并解析配置.

        Returns:
            TeamConfig: 解析后的配置对象

        Raises:
            FileNotFoundError: 配置文件不存在
            yaml.YAMLError: YAML 解析错误
        """
        if not self.config_path.exists():
            logger.warning(
                "[TeamConfigLoader] 配置文件不存在: %s，使用默认配置",
                self.config_path
            )
            return TeamConfig()

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not raw:
            logger.warning("[TeamConfigLoader] 配置文件为空，使用默认配置")
            return TeamConfig()

        # 展开环境变量
        raw = self._expand_env_vars(raw)
        
        # 获取 team 配置部分
        team_raw = raw.get("team", {})
        
        if not team_raw:
            logger.warning("[TeamConfigLoader] 配置文件中没有 team 配置，使用默认配置")
            return TeamConfig()

        # 解析 Leader 配置
        leader_raw = team_raw.get("leader", {})
        leader = LeaderConfig(
            member_name=leader_raw.get("member_name", "team_leader"),
            name=leader_raw.get("name", "TeamLeader"),
            persona=leader_raw.get("persona", "天才项目管理专家"),
            domain=leader_raw.get("domain", "project_management"),
        )

        # 解析预定义成员
        predefined_members = []
        for member_raw in team_raw.get("predefined_members", []):
            predefined_members.append(TeamMemberConfig(
                member_name=member_raw["member_name"],
                name=member_raw["name"],
                role_type=member_raw.get("role_type", "teammate"),
                persona=member_raw.get("persona", ""),
                domain=member_raw.get("domain", ""),
            ))

        # 解析传输层配置
        transport_raw = team_raw.get("transport", {})
        transport = TransportConfig(
            type=transport_raw.get("type", "team_runtime"),
            params=transport_raw.get("params", {}),
        )

        # 解析存储层配置
        storage_raw = team_raw.get("storage", {})
        storage_params = storage_raw.get("params", {})

        # 处理存储路径
        if "connection_string" in storage_params:
            conn_str = storage_params["connection_string"]
            
            # 如果是相对路径，则使用统一的 team_data 目录
            db_path = Path(conn_str)
            if not db_path.is_absolute():
                team_data_dir = get_agent_team_data_dir()
                db_path = team_data_dir / conn_str
                storage_params["connection_string"] = str(db_path)
            
            # 确保数据库目录存在
            db_dir = db_path.parent
            if not db_dir.exists():
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info("[TeamConfigLoader] Created database directory: %s", db_dir)

        storage = StorageConfig(
            type=storage_raw.get("type", "sqlite"),
            params=storage_params,
        )

        config = TeamConfig(
            team_name=team_raw.get("team_name", "jiuwen_team"),
            lifecycle=team_raw.get("lifecycle", "persistent"),
            teammate_mode=team_raw.get("teammate_mode", "build_mode"),
            spawn_mode=team_raw.get("spawn_mode", "coroutine"),
            leader=leader,
            predefined_members=predefined_members,
            transport=transport,
            storage=storage,
            metadata=team_raw.get("metadata", {}),
        )

        logger.info(
            "[TeamConfigLoader] 配置加载成功: team_name=%s, lifecycle=%s, members=%d",
            config.team_name,
            config.lifecycle,
            len(config.predefined_members),
        )

        return config


def load_team_config(config_path: Path | str | None = None) -> TeamConfig:
    """便捷函数：加载 Team 配置.

    Args:
        config_path: 配置文件路径，默认使用 resources/team_config.yaml

    Returns:
        TeamConfig: 解析后的配置对象
    """
    loader = TeamConfigLoader(config_path)
    return loader.load()