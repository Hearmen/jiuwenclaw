# 当前 Slash 命令现状表（基于 `gateway/slash_command.py`）

> 说明：以下为当前代码中的已登记命令。其中 `gateway` 表示在受控通道由 Gateway 解析/执行；`client` 表示仅客户端本地解析（当前阶段 Gateway 不执行）。

| id | canonical_text | scope | 当前 Gateway 行为 | req_method |
|------|------|------|------|------|
| `new_session` | `/new_session` | `gateway` | 仅整行精确匹配；命中后重置受控通道会话并拦截，不转发 Agent 对话。 | `-` |
| `mode` | `/mode plan\|agent\|fast\|team` | `gateway` | 仅白名单整行合法；命中后切换 `ChannelMode` 并写入 `params.mode`。 | `-` |
| `skills` | `/skills` | `gateway` | 仅整行精确匹配；命中后由 Gateway 触发技能查询并通知回复。 | `skills.list` |
| `resume` | `/resume` | `client` | 仅记录于首批注册表，当前阶段 IM 受控通道不解析。 | `command.resume` |
