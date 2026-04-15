# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RuntimePromptRail — Inject dynamic time/channel/runtime info per model call.

Dynamic content (time, channel, agent, model, language) is decoupled from the
static identity prompt and refreshed on every model call via before_model_call().
"""
from __future__ import annotations

import os
import platform
import subprocess
from shutil import which
from datetime import datetime, timedelta, timezone

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

_CN_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class RuntimePromptRail(DeepAgentRail):
    """在 before_model_call 中注入运行时动态 section（时间、运行时信息）。"""

    priority = 5  # 高优先级，确保早于其他 rail 执行

    def __init__(
        self,
        language: str = "cn",
        channel: str = "web",
        timezone_offset: int = 8,
        agent_name: str = "main_agent",
        model_name: str = "gpt-4",
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._language = language
        self._channel = channel
        self._tz = timezone(timedelta(hours=timezone_offset))
        self._agent_name = agent_name
        self._model_name = model_name

    def init(self, agent) -> None:
        """从 agent 获取 system_prompt_builder 引用。"""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """清理注入的 section 并释放引用。"""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("time")
            self.system_prompt_builder.remove_section("runtime")
        self.system_prompt_builder = None

    def set_language(self, language: str) -> None:
        """per-request 更新语言。"""
        self._language = language

    def set_channel(self, channel: str) -> None:
        """per-request 更新频道。"""
        self._channel = channel

    @staticmethod
    def _get_git_branch() -> str:
        """获取当前 git 分支名，失败时返回 N/A。"""
        git_bin = which("git")
        if not git_bin:
            return "N/A"

        try:
            result = subprocess.run(
                [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return "N/A"

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return "N/A"

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """每次 model call 注入最新的时间和运行时信息。"""
        if not self.system_prompt_builder:
            return

        now = datetime.now(tz=self._tz)
        if self._language == "cn":
            weekday_str = _CN_WEEKDAYS[now.weekday()]
            now_str = now.strftime("%Y-%m-%d") + f" {weekday_str} " + now.strftime("%H:%M:%S")
            time_content = f"# 当前日期与时间\n\n{now_str}"
        else:
            now_str = now.strftime("%Y-%m-%d %A %H:%M:%S")
            time_content = f"# Current Date & Time\n\n{now_str}"

        self.system_prompt_builder.add_section(PromptSection(
            name="time",
            content={"cn": time_content, "en": time_content},
            priority=92,
        ))

        cwd = os.getcwd()
        plat = f"{platform.system()} {platform.machine()}"
        python_ver = platform.python_version()
        git_branch = self._get_git_branch()

        if self._language == "cn":
            runtime_content = (
                "# 运行时\n\n"
                f"- 工作目录：{cwd}\n"
                f"- 平台：{plat}\n"
                f"- Python：{python_ver}\n"
                f"- 模型：{self._model_name}\n"
                f"- Git 分支：{git_branch}\n"
                f"- Agent：{self._agent_name}\n"
                f"- 频道：{self._channel}\n"
                f"- 语言：{self._language}"
            )
        else:
            runtime_content = (
                "# Runtime\n\n"
                f"- CWD: {cwd}\n"
                f"- Platform: {plat}\n"
                f"- Python: {python_ver}\n"
                f"- Model: {self._model_name}\n"
                f"- Git branch: {git_branch}\n"
                f"- Agent: {self._agent_name}\n"
                f"- Channel: {self._channel}\n"
                f"- Language: {self._language}"
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="runtime",
            content={"cn": runtime_content, "en": runtime_content},
            priority=95,
        ))
