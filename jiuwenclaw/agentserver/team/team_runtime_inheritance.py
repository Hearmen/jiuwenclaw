# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 成员运行时继承模块.

TeamMember 专用 Rail、Ability 继承逻辑，不依赖主 agent adapter。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
from openjiuwen.harness.rails.security_rail import SecurityRail
from openjiuwen.harness.rails.skill_evolution_rail import SkillEvolutionRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.rails.team_skill_rail import TeamSkillRail

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
    "TeamSkillRail",
    "SkillEvolutionRail",
})

TOOL_WHITELIST = frozenset({
    "free_search",
    "fetch_webpage",
    "paid_search",
    "vision",
    "audio",
    "image_ocr",
    "visual_question_answering",
    "generate_image",
    "audio_transcription",
    "audio_question_answering",
    "audio_metadata",
    "video_understanding",
    "search_skill",
    "install_skill",
    "uninstall_skill",
    "task_tool",
    "user_todos",
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
    role: str | None = None,
    team_ws_skills_dir: str | None = None,
) -> list[Any]:
    """为 Team 成员创建 rails 列表.

    Args:
        skills_dir: 成员 skills 目录路径，非 leader 时用于 SkillEvolutionRail
        language: 语言设置
        channel: 渠道设置（使用真实 channel_id）
        role: 成员角色，"leader" 时创建 TeamSkillRail
        team_ws_skills_dir: 团队共享 workspace skills 目录，leader 角色时使用

    Returns:
        rail 实例列表
    """
    rails_list = []

    try:
        rail = RuntimePromptRail(
            language=language,
            channel=channel,
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

    # Leader-only: TeamSkillRail for team skill evolution.
    if role == "leader" and team_ws_skills_dir:
        try:
            Path(team_ws_skills_dir).mkdir(parents=True, exist_ok=True)
            llm_model, actual_model_name = build_evolution_llm()
            team_skill_rail = TeamSkillRail(
                skills_dir=team_ws_skills_dir,
                llm=llm_model,
                model=actual_model_name,
                language=language,
                auto_save=False,
            )
            rails_list.append(team_skill_rail)
            logger.info(
                "[TeamRuntime] TeamSkillRail created: skills_dir=%s, model=%s",
                team_ws_skills_dir, actual_model_name,
            )
        except Exception as exc:
            logger.warning("[TeamRuntime] TeamSkillRail failed: %s", exc, exc_info=True)

    # Non-leader: SkillEvolutionRail for member skill self-evolution.
    if role != "leader" and skills_dir:
        evo_rail = build_skill_evolution_rail(skills_dir=skills_dir)
        if evo_rail is not None:
            rails_list.append(evo_rail)

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
            from jiuwenclaw.config import get_config
            config = get_config()
        except Exception as exc:
            logger.warning("[TeamRuntime] Failed to load config for default model: %s", exc)
            return "gpt-4"

    try:
        model_name = config.get("models", {}).get("default", {}).get(
            "model_client_config", {}
        ).get("model_name")
        if model_name:
            return model_name
    except Exception as exc:
        logger.warning("[TeamRuntime] Failed to resolve default model name: %s", exc)

    return "gpt-4"


def resolve_model_config(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """从配置字典解析 model 相关参数.

    Args:
        config: 配置字典.

    Returns:
        (model_client_config dict, model_config_obj dict, model_name str).
    """
    model_configs = config.get("models", {}).copy()
    default_model_config = model_configs.get("default", {}).copy()
    react_config = config.get("react", {}).copy()

    model_client_config = default_model_config.get("model_client_config") or {}
    if not model_client_config:
        model_client_config = react_config.get("model_client_config") or {}

    model_name = (
        model_client_config.get("model_name")
        or react_config.get("model_name")
        or "gpt-4"
    )

    model_config_obj = default_model_config.get("model_config_obj") or {}
    if not model_config_obj:
        model_config_obj = react_config.get("model_config_obj") or {}

    return model_client_config, model_config_obj, model_name


def build_evolution_llm(
    config: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    """从配置构造 evolution 使用的 LLM Model 实例.

    Args:
        config: 可选配置字典，为 None 时自动加载.

    Returns:
        (Model 实例, model_name 字符串) 元组.
    """
    from openjiuwen.core.foundation.llm import (
        Model, ModelClientConfig, ModelRequestConfig,
    )

    if config is None:
        from jiuwenclaw.config import get_config
        config = get_config()

    model_client_config, model_config_obj, model_name = resolve_model_config(config)

    request_config = ModelRequestConfig(
        model=model_name,
        temperature=model_config_obj.get("temperature", 0.95),
    )
    client_config = ModelClientConfig(**model_client_config)
    return Model(model_client_config=client_config, model_config=request_config), model_name


def build_skill_evolution_rail(
    skills_dir: str,
    config: dict[str, Any] | None = None,
) -> Any | None:
    """为 Team member 构造 SkillEvolutionRail.

    Args:
        skills_dir: 技能目录路径.
        config: 可选配置字典.

    Returns:
        SkillEvolutionRail 实例，失败返回 None.
    """
    try:
        llm, model_name = build_evolution_llm(config)
        _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
        if _env_auto_scan is not None:
            evolution_auto_scan: bool = _env_auto_scan.lower() in ("true", "1", "yes")
        else:
            evolution_auto_scan = (config or {}).get("evolution", {}).get("auto_scan", False)

        rail = SkillEvolutionRail(
            skills_dir=skills_dir,
            llm=llm,
            model=model_name,
            auto_scan=evolution_auto_scan,
            auto_save=True,
        )
        logger.info(
            "[TeamRuntime] SkillEvolutionRail created: model=%s, auto_scan=%s",
            model_name,
            evolution_auto_scan,
        )
        return rail
    except Exception as exc:
        logger.warning("[TeamRuntime] SkillEvolutionRail creation failed: %s", exc, exc_info=True)
        return None
