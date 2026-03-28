from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MemoryHookContext:
    session_id: str
    request_id: str
    channel_id: str | None
    agent_name: str
    workspace_dir: str
    assistant_message: str | None = None
    # 输入扩展
    extra: dict[str, Any] = field(default_factory=dict)
    # 记忆内容（before_chat 扩展写入，宿主从本字段读取拼接结果）
    memory_blocks: list[str] = field(default_factory=list)
    # 输出扩展
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
