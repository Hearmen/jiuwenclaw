# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Rails for DeepAgent integration."""

from jiuwenclaw.agentserver.deep_agent.rails.context_engineering_rail_ext import JiuClawContextEngineeringRail
from jiuwenclaw.agentserver.deep_agent.rails.permission_rail import PermissionInterruptRail
from jiuwenclaw.agentserver.deep_agent.rails.avatar_rail import AvatarPromptRail
from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail
from jiuwenclaw.agentserver.deep_agent.rails.stream_event_rail import JiuClawStreamEventRail

__all__ = [
    "JiuClawContextEngineeringRail",
    "JiuClawStreamEventRail",
    "PermissionInterruptRail",
    "AvatarPromptRail",
    "RuntimePromptRail",
]
