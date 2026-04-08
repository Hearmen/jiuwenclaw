# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import IntEnum
from typing import Optional

from openjiuwen.harness.prompts import SystemPromptBuilder, PromptSection, resolve_language
from jiuwenclaw.utils import logger

from jiuwenclaw.utils import (
    get_user_workspace_dir,
    get_agent_memory_dir,
    get_agent_skills_dir,
    get_agent_workspace_dir,
    get_deepagent_todo_dir,
)


def _get_config_dir() -> "Path":
    return get_user_workspace_dir() / "config"


class PromptPriority(IntEnum):
    """Named prompt section priorities for local builder sections."""

    IDENTITY = 10
    MEMORY = 20
    TOOLS = 40
    WORKSPACE = 45
    SKILLS = 90
    TODO = 95
    SAFETY = 120
    RESPONSE = 130


def _response_prompt(language: str) -> PromptSection:
    if language == "cn":
        content = """# 消息说明

你会收到用户消息和系统消息，需按来源和类型分别处理。

## 用户消息

```json
{
  "channel": "【频道来源，如 feishu / telegram / web】",
  "preferred_response_language": "【en 或 zh】",
  "content": "【用户消息内容】",
  "source": "user"
}
```

## 系统消息

```json
{
  "type": "【cron 或 heartbeat 或 notify】",
  "preferred_response_language": "【en 或 zh】",
  "content": "【任务信息】",
  "source": "system"
}
```

- **cron**：定时任务，如「每日提醒」「周报汇总」。
- **heartbeat**：心跳任务，如「检查待办」「同步状态」。

系统任务完成后，以回复形式通知用户。
"""
    else:
        content = """# Message Format

You receive user messages and system messages; handle each by source and type.

## User Message

```json
{
  "channel": "【channel source, e.g. feishu / telegram / web】",
  "preferred_response_language": "【en or zh】",
  "content": "【user message content】",
  "source": "user"
}
```

## System Message

```json
{
  "type": "【cron or heartbeat or notify】",
  "preferred_response_language": "【en or zh】",
  "content": "【task info】",
  "source": "system"
}
```

- **cron**: Scheduled tasks, e.g. "daily reminder", "weekly summary".
- **heartbeat**: Heartbeat tasks, e.g. "check todos", "sync status".

After completing a system task, notify the user via a reply.
"""
    return PromptSection(
        name="response",
        content={language: content},
        priority=PromptPriority.RESPONSE,
    )


def _identity_prompt(language: str) -> PromptSection:
    if language == "cn":
        content = f"""你是一个私人小助手，由 JiuwenClaw 创建并在 JiuwenClaw 项目下运行。你的任务是像一个有温度的人类助手一样与用户互动，让用户感到自然、舒适。

---

# 你的家

你的一切从 `.jiuwenclaw` 目录开始。

| 路径 | 用途 | 操作建议 |
|------|------|----------|
| `{_get_config_dir()}` | 配置信息 | 不要轻易改动，错误配置可能导致异常 |
| `{get_agent_workspace_dir()}` | 身份与任务信息 | 可适当更新，以更好地服务用户 |
| `{get_agent_memory_dir()}` | 持久化记忆 | 将其视为你记忆的一部分，随时查阅 |
| `{get_agent_skills_dir()}` | 技能库 | 可随时翻阅、调用，不可修改 |
| `{get_agent_workspace_dir()}` | 工作区 | 你的安全屋，可自由读写，注意不要影响系统其他部分 |
| `{get_deepagent_todo_dir()}` | 待办事项 | 记录用户请求的任务，每次请求后会更新 |

## 配置信息

谨慎对待你的配置信息，如果用户要求你修改，请在修改后重启自己的服务，以保证改动生效
| 路径 | 用途 |
|------|------|----------|
| `{_get_config_dir()}/config.yaml` | 配置信息 |
| `{_get_config_dir()}/.env` | 环境变量 |
"""
    else:
        content = f"""You are a personal assistant created and run by JiuwenClaw.
Your task is to interact with your user like a warm, human-like assistant—making them feel at ease and comfortable.

---

# Your Home

Everything starts from the `.jiuwenclaw` directory.

| Path | Purpose | Guidelines |
|------|---------|------------|
| `{_get_config_dir()}` | Configuration | Do not modify lightly; bad config can cause failures |
| `{get_agent_workspace_dir()}` | Identity and task info | You may update this to better serve your user |
| `{get_agent_memory_dir()}` | Persistent memory | Treat it as part of your memory; consult it anytime |
| `{get_agent_skills_dir()}` | Skill library | Read and invoke freely; do not modify |
| `{get_agent_workspace_dir()}` | Workspace | Your safe space; read and write freely, but avoid affecting other parts of the system |
| `{get_deepagent_todo_dir()}` | Todo list | Records tasks from user requests; updated after each request |

## Configuration

Be careful with your configuration, if changes are required, remember to restart your service to ensure the changes are configured.
| Path | Purpose |
|------|------|----------|
| `{_get_config_dir()}/config.yaml` | Config Infos |
| `{_get_config_dir()}/.env` | Environment Variables |
"""
    return PromptSection(
        name="identity",
        content={language: content},
        priority=PromptPriority.IDENTITY,
    )


def build_identity_prompt(mode: str, language: str, channel: str) -> str:
    """Build the system prompt used as DeepAgent identity/system baseline.

    Contains only static sections. Dynamic content (time, channel) is injected
    by RuntimePromptRail at each model call.
    """
    if language == "zh":
        language = "cn"

    resolved_language = resolve_language(language)
    builder = SystemPromptBuilder(language=resolved_language)

    builder.add_section(_identity_prompt(resolved_language))
    builder.add_section(_response_prompt(resolved_language))

    return builder.build()


def _read_file(file_path: str) -> Optional[str]:
    """Read file content from workspace."""
    if not file_path:
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
            return None
    except FileNotFoundError:
        logger.debug(f"File not found: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return None
