# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interrupt helpers for DeepAgent.

Provides utilities for converting interrupt payloads to frontend format
and building permission rails.
"""
from __future__ import annotations

from typing import Any

from jiuwenclaw.agentserver.permissions.checker import collect_permission_rail_tool_names
from jiuwenclaw.agentserver.permissions.core import get_permission_engine
from jiuwenclaw.utils import logger


def build_permission_rail(
    config: dict[str, Any],
    llm: Any = None,
    model_name: str | None = None,
) -> Any | None:
    """Build PermissionInterruptRail for tool permission checks.

    Args:
        config: Agent config dict containing permissions section
        llm: LLM instance for risk assessment
        model_name: Model name for risk assessment

    Returns:
        PermissionInterruptRail instance or None if disabled
    """
    from jiuwenclaw.agentserver.deep_agent.rails.permission_rail import PermissionInterruptRail

    permission_config = config.get("permissions", {})
    logger.info(
        "[InterruptHelpers] build_permission_rail called: enabled=%s",
        permission_config.get("enabled", False)
    )

    if not permission_config.get("enabled", False):
        logger.info("[InterruptHelpers] Permission system is disabled, returning None")
        return None

    tools_config = permission_config.get("tools", {})
    tool_names = collect_permission_rail_tool_names(permission_config)
    logger.info(
        "[InterruptHelpers] tools_config keys: %s, rail tool_names (with rules): %s",
        list(tools_config.keys()),
        tool_names,
    )
    logger.info(
        "[InterruptHelpers] Building PermissionInterruptRail with tool_names=%s llm=%s model_name=%s",
        tool_names, llm is not None, model_name,
    )
    try:
        permission_rail = PermissionInterruptRail(
            config=permission_config,
            engine=get_permission_engine(),
            tool_names=tool_names,
            llm=llm,
            model_name=model_name,
        )
        logger.info(
            "[InterruptHelpers] PermissionInterruptRail created successfully with tool_names=%s",
            tool_names
        )
    except Exception as exc:
        logger.warning("[InterruptHelpers] PermissionInterruptRail create failed: %s", exc)
        permission_rail = None
    return permission_rail



def convert_interactions_to_ask_user_question(state_outputs: list) -> dict | None:
    """Convert __interaction__ list to frontend chat.ask_user_question format.

    AskUserRail 中断: value 有 questions 字段 → source="ask_user_interrupt"
    PermissionRail 中断: value 无 questions 字段 → source="permission_interrupt"

    state_outputs 中的元素可能是:
    - InteractionOutput 对象 (有 id, value 属性, value 是 ToolCallInterruptRequest)
    - dict (有 id, value 键)
    """
    if not state_outputs:
        return None

    interaction = state_outputs[0]
    if hasattr(interaction, "id"):
        request_id = interaction.id
        value_obj = interaction.value
    elif isinstance(interaction, dict):
        request_id = interaction.get("id", "")
        value_obj = interaction.get("value", {})
    else:
        return None

    questions_raw = _extract_questions_from_value(value_obj)

    if questions_raw is not None:
        questions = _build_multi_questions(questions_raw)
        return {
            "event_type": "chat.ask_user_question",
            "request_id": request_id,
            "questions": questions,
            "source": "ask_user_interrupt",
        }

    question_data = extract_question_from_interaction(interaction)
    if not question_data:
        return None

    return {
        "event_type": "chat.ask_user_question",
        "request_id": request_id,
        "questions": [question_data],
        "source": "permission_interrupt",
    }


def _extract_questions_from_value(value_obj: Any) -> list | None:
    """从 value 对象中提取 questions 列表.

    AskUserRail 的 value (ToolCallInterruptRequest) 有 questions 属性.
    如果 questions 存在且非空, 返回列表; 否则返回 None 表示不是 AskUserRail 中断.
    """
    if hasattr(value_obj, "questions"):
        qs = value_obj.questions
        if qs and len(qs) > 0:
            return qs
    elif isinstance(value_obj, dict):
        qs = value_obj.get("questions", [])
        if qs and len(qs) > 0:
            return qs
    return None


def _build_multi_questions(questions_data: list) -> list:
    """Build frontend PendingQuestionItem list from questions data.

    有选项的问题: 保留原始选项 + 追加 __other__ (自定义输入)
    无选项的问题: 不追加 __other__, 前端应直接进入自由输入模式
    """
    questions = []
    for q in questions_data:
        raw_options = q.get("options", [])
        if raw_options:
            options = [{"label": opt["label"], "description": opt.get("description", "")}
                       for opt in raw_options]
            options.append({"label": "Other", "description": "Custom input"})
        else:
            options = []
        questions.append({
            "question": q["question"],
            "header": q["header"],
            "options": options,
            "multi_select": q.get("multi_select", False),
        })
    return questions


def extract_question_from_interaction(payload: Any) -> dict | None:
    """Extract question info from a single interaction payload.

    Args:
        payload: InteractionOutput instance or dict

    Returns:
        Question format dict for frontend
    """
    if payload is None:
        return None

    tool_name = ""
    message = ""

    if hasattr(payload, 'value'):
        value_obj = payload.value
        message = getattr(value_obj, 'message', '') or getattr(value_obj, 'question', '')
        tool_name = getattr(value_obj, 'tool_name', '')
    elif isinstance(payload, dict):
        value_obj = payload.get('value', {})
        if isinstance(value_obj, dict):
            message = value_obj.get('message', '') or value_obj.get('question', '')
            tool_name = value_obj.get('tool_name', '')
        else:
            message = payload.get('message', '') or payload.get('question', '')
    else:
        return None

    return {
        "question": message or f"工具 `{tool_name}` 需要授权才能执行",
        "header": f"权限审批: {tool_name}" if tool_name else "权限审批",
        "options": [
            {"label": "本次允许", "description": "仅本次授权执行"},
            {"label": "总是允许", "description": "记住该规则，以后自动放行"},
            {"label": "拒绝", "description": "拒绝执行此工具"},
        ],
        "multi_select": False,
    }
