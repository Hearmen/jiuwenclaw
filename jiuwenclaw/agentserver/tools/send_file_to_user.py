# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Send File Toolkit

提供发送文件到用户的工具。支持发送一个或多个文件。

使用方式：
1. 创建 SendFileToolkit 实例
2. 调用 get_tools() 获取工具列表
3. 工具会自动注册到 Runner 中
"""

from __future__ import annotations

import json
import os
import logging
from typing import List, Union

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer

logger = logging.getLogger(__name__)


class SendFileToolkit:
    """Toolkit for sending files to users."""

    def __init__(self, request_id: str, session_id: str, channel_id: str) -> None:
        """Initialize SendFileToolkit.

        Args:
            request_id: Request identifier for message routing.
            session_id: Session identifier for message routing.
            channel_id: Channel identifier for message routing.
        """
        self.request_id = request_id
        self.session_id = session_id
        self.channel_id = channel_id
        logger.debug(
            "[SendFileToolkit] 初始化 request_id=%s session_id=%s channel_id=%s",
            request_id,
            session_id,
            channel_id,
        )

    async def send_file(self, abs_file_path_list: Union[List[str], str]) -> str:
        """Send files to user.

        Args:
            abs_file_path_list: List of absolute file paths to send.

        Returns:
            Success message or error description.
        """
        if isinstance(abs_file_path_list, str):
            try:
                parsed = json.loads(abs_file_path_list)
                if isinstance(parsed, list):
                    abs_file_path_list = parsed
                elif isinstance(parsed, str):
                    abs_file_path_list = [parsed]
                else:
                    abs_file_path_list = [abs_file_path_list]
            except (TypeError, ValueError):
                abs_file_path_list = [abs_file_path_list]

        if not isinstance(abs_file_path_list, list):
            abs_file_path_list = [str(abs_file_path_list)]

        valid_files = []
        missing_files = []
        for fp in abs_file_path_list:
            fp = str(fp).strip()
            if not fp:
                continue
            if os.path.isfile(fp):
                valid_files.append(fp)
            else:
                missing_files.append(fp)
                logger.warning("[SendFileToolkit] 文件不存在: %s", fp)

        if not valid_files:
            msg_parts = ["发送文件失败：所有文件均不存在"]
            for mf in missing_files:
                msg_parts.append(f"  - {mf}")
            return "\n".join(msg_parts)

        logger.info(
            "[SendFileToolkit] send_file 开始 session_id=%s 有效文件=%d 缺失=%d",
            self.session_id,
            len(valid_files),
            len(missing_files),
        )

        try:
            server = AgentWebSocketServer.get_instance()
            files_payload = [
                {
                    "path": file_path,
                    "name": os.path.basename(file_path),
                }
                for file_path in valid_files
            ]
            msg = {
                "request_id": self.request_id,
                "channel_id": self.channel_id,
                "session_id": self.session_id,
                "payload": {
                    "event_type": "chat.file",
                    "files": files_payload,
                },
                "is_complete": False,
            }
            await server.send_push(msg)
            result_parts = [f"成功发送 {len(valid_files)} 个文件"]
            if missing_files:
                result_parts.append("以下文件不存在，未发送：")
                for mf in missing_files:
                    result_parts.append(f"  - {mf}")
            return "\n".join(result_parts)
        except Exception as e:
            logger.exception(
                "[SendFileToolkit] send_file 失败 session_id=%s error=%s",
                self.session_id,
                str(e),
            )
            return f"提交文件失败: {str(e)}"

    def get_tools(self) -> List[Tool]:
        """Return tools for registration in Runner.

        Returns:
            List of tools for sending files.
        """
        session_id = self.session_id

        def make_tool(
            name: str,
            description: str,
            input_params: dict,
            func,
        ) -> Tool:
            card = ToolCard(
                id=f"{name}_{session_id}_{self.request_id}",
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="send_file_to_user",
                description=(
                    "发送文件给用户。支持发送一个或多个文件。"
                    "需要提供文件的绝对路径列表。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "abs_file_path_list": {
                            "type": ["array", "string"],
                            "items": {"type": "string"},
                            "description": "要发送的文件绝对路径列表",
                        }
                    },
                    "required": ["abs_file_path_list"],
                },
                func=self.send_file,
            ),
        ]
