# Slash 命令速查表

本文档按**解析位置**拆分：`TUI 本地解析` 与 `Gateway / Agent 侧解析`。  
用于快速查阅当前行为，最终实现以代码为准。

---

## 一览：按解析侧区分

### TUI 本地解析（CLI 内置）

在终端 UI 本地执行，不走 Gateway 受控命令管线。

| 命令 | 说明 |
|---|---|
| `/clear` | 清屏 |
| `/color` | 调整 TUI 配色 |
| `/copy` | 复制上一条消息 |
| `/exit` | 退出 |
| `/help` | 查看可用命令 |
| `/theme` | 切换主题 |
| `/config` | 修改配置（当前为本地实现，后续计划统一到 Gateway） |
| `/workspace` | 管理工作区路径与目录授权（见下文） |

> 说明：`/mode` 的受控切换逻辑以 Gateway 侧行为为主，详见下文「`/mode` 与 `/switch`」。

### Gateway / Agent 侧解析（受控通道）

由 Gateway 识别并转发到 AgentServer 等后端能力。

| 命令 | 说明 |
|---|---|
| `/add_dir` | 目录授权为可读写（TUI 侧建议改用 `/workspace add`） |
| `/plan` | 切换规划子模式 |
| `/resume` | 历史会话恢复（见下文） |
| `/new_session` | 新建会话（仅 IM 生效） |
| `/mode` | 模式切换（支持一级入口与直达写法） |
| `/switch` | 在当前模式族内切换二级模式 |
| `/skills` | 技能列表（见「IM 与 TUI 差异」） |
| `/model` | 模型查看、新增、切换（见下文） |
| `/diff` | 查看当前会话按轮次改动（见下文） |

---

## 重点命令说明

### `/workspace`（TUI 工作区目录、待开发）

- 常用子命令：
  - `/workspace` 或 `/workspace get`：查看当前路径；
  - `/workspace set <path>`：设置工作区路径（支持如 `./` 的相对路径，按 TUI 启动目录解析）；
  - `/workspace clear`：清空工作区路径；
  - `/workspace add <path>`：迁移自原 `/add-dir`，底层调用 `command.add_dir`。
- 兼容别名：`/workspace_dir`、`/workspace-dir`。
- 持久化文件：`~/.jiuwenclaw/tui-workspace-dir`（单行文本）。
- 默认值：若本地未保存，TUI 启动时默认取 `process.cwd()`。
- 与 Gateway 关系：当存在非空工作区路径时，TUI 通过 `sendEventOnly` 发送请求会在 `params` 中附带 `workspace_dir`，供 Gateway / AgentServer 使用。
- 切换路径与会话隔离：
  - 仅当新旧路径（规范化后）不同才触发处理；
  - 当前会话已发生 `chat.send` 时，`/workspace set` 才进入“确认 ->（必要时）中断 -> 新建会话”流程；
  - 若尚未 `chat.send`，仅更新 `workspace_dir`，不新建会话。

### `/mode` 与 `/switch`（受控通道）

- 一级入口映射：
  - `/mode agent` -> `agent.plan`
  - `/mode code` -> `code.normal`
  - `/mode team` -> `team`
- 直达写法：
  - `/mode agent.plan` -> `agent.plan`
  - `/mode agent.fast` -> `agent.fast`
  - `/mode code.plan` -> `code.plan`
  - `/mode code.normal` -> `code.normal`
- 二级切换：
  - agent 族：`/switch plan` <-> `agent.plan`，`/switch fast` <-> `agent.fast`
  - code 族：`/switch plan` <-> `code.plan`，`/switch normal` <-> `code.normal`
- 非法组合（如在 `code.*` 下执行 `/switch fast`）返回：`非法指令`。
- 备注：独立 `/team` 命令已移除，请统一使用 `/mode team`。

### `/resume`

- `/resume list`：列出历史会话。
- `/resume <conversation_id>`：恢复指定会话。

### `/model`（查看 / 新增 / 切换模型）

- 用法：
  - `/model` 或 `/model list`：列出可切换模型（含当前模型标记）；
  - `/model <name>`：切换到指定模型；
  - `/model add <name> key=value ...`：新增模型配置（如 `model=...`、`provider=...`、`api_base=...`、`api_key=...`）。
- 限制：`video` / `audio` / `vision` 不能通过 `/model <name>` 设置为默认聊天模型，需改用 `/config edit` 或 `/config set`。
- 配置写入行为：
  - 新增模型会写入 `config.yaml` 的 `models.defaults`（兼容旧结构），并触发 Agent 配置重载；
  - 切换模型会校验配置与环境变量占位符，更新 `MODEL_NAME` / `MODEL_PROVIDER` / `API_BASE` / `API_KEY`，并回写 `.env`。
- 安全展示：涉及 `api_key`、`token` 等敏感字段会掩码显示。

### `/diff`（会话改动回顾）

- 用法：`/diff`（无子命令）。
- 数据来源：TUI 通过 `command.diff` 请求 Agent 侧 diff 服务，按当前 `session_id` 返回 `turns`（每轮改动集合）。
- 展示规则：
  - 有改动：显示 `Found N turn(s) with file changes` 并附结构化 `turns`；
  - 无改动：显示 `No file changes in this session`。
- 作用范围：用于查看当前会话内未提交的按轮次改动轨迹，不替代 `git diff` 的完整版本控制视角。

---

## `/skills`：IM 与 TUI 的差异

两端最终都会请求 `skills.list`，但触发方式和展示形态不同。

| 端 | 触发方式 | 行为 |
|---|---|---|
| IM（飞书等受控通道） | 整行精确匹配 `/skills list`（会先做空白规范化） | Gateway 拦截控制消息并请求 `skills.list`，结果以 IM 通知/卡片等形式展示；单独输入 `/skills` 不走该控制路径。 |
| TUI（CLI 内置） | 输入 `/skills` | 本地执行内置命令并调用 `skills.list`，在会话内以列表视图展示（标题 `Skills`）；无数据时提示 `No skills returned`。 |

结论：IM 使用 `/skills list`，TUI 使用 `/skills`，当前写法存在差异（已知现状）。

---

## 待开发

| 命令             | 说明      |
|----------------|---------|
| `/compact`     | 压缩当前上下文 |
| `/init`        | 项目初始化   |
| `/btw`         | 提问      |
| `/context`     | 上下文状态查看 |
| `/export`      | 导出相关文件  |
| `/mcp`         | mcp管理   |
| `/memory`      | 记忆管理    |
| `/permissions` | 权限管理    |


