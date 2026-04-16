# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 成员运行时继承模块.

TeamMember 专用 Rail、Ability 继承逻辑，不依赖主 agent adapter。
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
from openjiuwen.harness.rails.security_rail import SecurityRail
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

from jiuwenclaw.agentserver.deep_agent.rails.avatar_rail import AvatarPromptRail
from jiuwenclaw.agentserver.deep_agent.rails.response_prompt_rail import ResponsePromptRail
from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail
from jiuwenclaw.agentserver.deep_agent.rails.stream_event_rail import JiuClawStreamEventRail

logger = logging.getLogger(__name__)

RAIL_WHITELIST = frozenset({
    "RuntimePromptRail",
    "ResponsePromptRail",
    "JiuClawStreamEventRail",
    "TaskPlanningRail",
    "SecurityRail",
    "HeartbeatRail",
    "AvatarPromptRail",
    "FileSystemRail",
    "SkillUseRail",
})

TOOL_WHITELIST = frozenset({
    "free_search",
    "fetch_webpage",
    "paid_search",
    "vision",
    "audio",
    "video_understanding",
    "search_skill",
    "install_skill",
    "uninstall_skill",
    "get_user_location",
    "create_note",
    "search_notes",
    "modify_note",
    "create_calendar_event",
    "search_calendar_event",
    "search_contact",
    "search_photo_gallery",
    "upload_photo",
    "search_file",
    "upload_file",
    "call_phone",
    "send_message",
    "search_message",
    "create_alarm",
    "search_alarms",
    "modify_alarm",
    "delete_alarm",
    "xiaoyi_collection",
    "image_reading",
    "xiaoyi_gui_agent",
})


def build_member_rails(
    skills_dir: str,
    language: str = "cn",
    channel: str = "default",
    agent_name: str = "team_member",
    model_name: str = "gpt-4",
) -> list[Any]:
    """为 Team 成员创建 rails 列表.

    Args:
        skills_dir: 成员 workspace 下的 skills 目录路径
        language: 语言设置
        channel: 渠道设置（使用真实 channel_id）
        agent_name: 成员名称
        model_name: 模型名称

    Returns:
        rail 实例列表
    """
    rails_list = []

    try:
        rail = RuntimePromptRail(
            language=language,
            channel=channel,
            agent_name=agent_name,
            model_name=model_name,
        )
        rails_list.append(rail)
        logger.info("[TeamRuntime] RuntimePromptRail created: channel=%s", channel)
    except Exception as exc:
        logger.warning("[TeamRuntime] RuntimePromptRail failed: %s", exc)

    try:
        rail = ResponsePromptRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] ResponsePromptRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] ResponsePromptRail failed: %s", exc)

    try:
        rail = FileSystemRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] FileSystemRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] FileSystemRail failed: %s", exc)

    try:
        rail = SkillUseRail(
            skills_dir=skills_dir,
            skill_mode="all",
        )
        rails_list.append(rail)
        logger.info("[TeamRuntime] SkillUseRail created: %s", skills_dir)
        
        try:
            loaded_skills = getattr(rail, '_skills', None)
            if loaded_skills is not None:
                logger.info("[TeamRuntime] SkillUseRail loaded %d skills: %s", 
                           len(loaded_skills), list(loaded_skills.keys()) if isinstance(loaded_skills, dict) else loaded_skills)
            else:
                logger.info("[TeamRuntime] SkillUseRail skills not directly accessible, checking via manager...")
        except Exception:
            logger.info("[TeamRuntime] Cannot inspect loaded skills from SkillUseRail")
    except Exception as exc:
        logger.warning("[TeamRuntime] SkillUseRail failed: %s", exc)

    try:
        rail = JiuClawStreamEventRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] JiuClawStreamEventRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] JiuClawStreamEventRail failed: %s", exc)

    try:
        rail = TaskPlanningRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] TaskPlanningRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] TaskPlanningRail failed: %s", exc)

    try:
        rail = SecurityRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] SecurityRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] SecurityRail failed: %s", exc)

    try:
        rail = HeartbeatRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] HeartbeatRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] HeartbeatRail failed: %s", exc)

    try:
        rail = AvatarPromptRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] AvatarPromptRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] AvatarPromptRail failed: %s", exc)

    logger.info("[TeamRuntime] Total rails built: %d", len(rails_list))
    return rails_list


def filter_inheritable_ability_cards(main_agent: Any) -> list[ToolCard]:
    """从主 agent 获取可继承的 ToolCard 白名单.

    Args:
        main_agent: 主 DeepAgent 实例

    Returns:
        白名单内的 ToolCard 列表
    """
    result = []
    try:
        abilities = main_agent.ability_manager.list()
        for ability in abilities:
            if isinstance(ability, ToolCard):
                if ability.name in TOOL_WHITELIST:
                    result.append(ability)
                else:
                    logger.debug("[TeamRuntime] Tool '%s' not in whitelist, skipped", ability.name)
            else:
                logger.debug(
                    "[TeamRuntime] Skipping non-ToolCard ability: %s",
                    getattr(ability, "name", type(ability)),
                )
    except Exception as exc:
        logger.warning("[TeamRuntime] Failed to filter inheritable abilities: %s", exc)
    return result


def get_default_model_name(config: dict[str, Any] | None = None) -> str:
    """从配置获取默认 model_name.

    Args:
        config: 可选的配置字典

    Returns:
        model_name 字符串，默认为 "gpt-4"
    """
    if config is None:
        try:
            from jiuwenclaw.agentserver.config import get_config
            config = get_config()
        except Exception:
            return "gpt-4"

    try:
        model_name = config.get("models", {}).get("default", {}).get(
            "model_client_config", {}
        ).get("model_name")
        if model_name:
            return model_name
    except Exception:
        pass

    return "gpt-4"
