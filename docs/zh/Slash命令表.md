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
| `/workspace` | 管理可信目录（见下文） |

> 说明：`/mode` 的受控切换逻辑以 Gateway 侧行为为主，详见下文「`/mode` 与 `/switch`」。

### Gateway / Agent 侧解析（受控通道）

由 Gateway 识别并转发到 AgentServer 等后端能力。

| 命令 | 说明 |
|---|---|
| `/plan` | 切换规划子模式 |
| `/resume` | 历史会话恢复（见下文） |
| `/new_session` | 新建会话（仅 IM 生效） |
| `/mode` | 模式切换（支持一级入口与直达写法） |
| `/switch` | 在当前模式族内切换二级模式 |
| `/skills` | 技能列表（见「IM 与 TUI 差异」） |
| `/model` | 模型查看、新增、切换（见下文） |
| `/mcp` | MCP 服务管理（见下文） |
| `/diff` | 查看当前会话按轮次改动（见下文） |
| `/compact` | 压缩当前上下文（见下文） |
| `/init` | 项目初始化（见下文） |

---

## 重点命令说明

### `/workspace`（TUI 可信目录管理）

管理 AI 可访问的目录范围，用于文件读取、编辑、执行等操作。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/workspace` 或 `/workspace get` | 查看系统默认工作空间与当前可信目录列表 |
| `/workspace add [path]` | 添加可信目录（默认为当前目录，路径不存在时提示错误） |
| `/workspace set <path>` | 重置可信目录为单个路径（已有可信目录时需确认） |
| `/workspace remove <path>` | 移除指定可信目录 |
| `/workspace clear` | 清空所有可信目录（仅使用默认工作空间） |

#### 概念说明

- **系统默认工作空间（workspace）**：固定路径 `~/.jiuwenclaw/agent/jiuwenclaw_workspace`，始终可用
- **可信目录（trusted_dirs）**：用户授权的可访问目录，由 TUI 管理，传递给后端 Agent

#### 控制逻辑

1. **启动确认**：TUI 启动时询问用户是否信任当前目录
   - 选择「信任」：将当前目录添加为可信目录
   - 选择「不信任」：仅使用默认工作空间

2. **会话级管理**：可信目录在当前 CLI 会话有效，不持久化到文件

3. **后端传递**：TUI 通过请求参数 `trusted_dirs` 传递可信目录列表，Agent 据此限制文件操作范围

4. **路径限制**：Agent 收到可信目录后，文件操作需限制在可信目录范围内；超出范围需向用户确认

5. **路径校验**：`add` 和 `set` 操作会校验路径是否存在，不存在则提示错误

#### 兼容别名

`/workspace_dir`、`/workspace-dir`

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

### `/compact`（上下文压缩）

- 用法：`/compact`（无参数）。
- 功能：主动触发上下文压缩，清理对话历史但保留摘要信息在上下文中。
- 数据来源：TUI 通过 `command.compact` 请求 Agent 侧压缩服务。
- 返回结果：
  - `busy`：压缩正在进行中，请稍后重试；
  - `compressed`：压缩成功，显示压缩前后 token 数及节省比例；
  - `noop`：无需压缩，上下文已处于最优状态。

### `/init`（项目初始化）

- 用法：`/init`（无参数）。
- 功能：初始化项目 AI 协作配置，生成 `JIUWENCLAW.md` 和可选的 `JIUWENCLAW.local.md`。
- 适用范围：仅在 `code` 模式下运行。
- 流程：
  1. 选择范围：`团队共享`（JIUWENCLAW.md）、`个人私有`（JIUWENCLAW.local.md）或 `都要`。
  2. 检测已有配置：自动检测 `CLAUDE.md`、`.cursorrules`、`copilot-instructions.md` 等文件。
  3. 生成配置：根据选择生成项目配置文件。
- 自动模式切换：若当前处于 `code.plan` 模式，会自动切换到 `code.normal` 以便写入文件。

### `/mcp`（MCP 服务管理）

- 用法：
  - `/mcp list`：列出全部 MCP 服务（名称、transport、启用状态）；
  - `/mcp show [name]`：查看 MCP 配置；不带参数时展示当前启用项，带 `name` 时展示单个服务详情；
  - `/mcp add --name <name> --transport <stdio|sse> ...`：新增 MCP 服务；
  - `/mcp update --name <name> ...`：更新指定 MCP 服务配置（支持更新 transport / 参数 / 启用状态）；
  - `/mcp enable <name>`：启用指定 MCP 服务；
  - `/mcp disable <name>`：禁用指定 MCP 服务；
  - `/mcp remove <name>`：删除指定 MCP 服务。
- 传输参数：
  - `stdio`：需提供 `--command`，可选 `--args`、`--cwd`、`--env`；
  - `sse`：需提供 `--url`，可选 `--headers`、`--timeout_s`。
- 示例：
  - `/mcp list`
  - `/mcp show`
  - `/mcp show playwright`
  - `/mcp add --name playwright --transport stdio --command python --args "server.py --transport stdio"`
  - `/mcp update --name playwright --transport sse --url http://127.0.0.1:9000/sse --headers "Authorization=Bearer xxx"`
  - `/mcp add --name local-sse --transport sse --url http://127.0.0.1:9000/sse`
  - `/mcp disable playwright`
  - `/mcp remove local-sse`
- 配置与生效：
  - 变更会写入 `config.yaml` 的 `mcp.servers`；
  - 写入后会触发 Agent 配置重载，运行时按配置同步 MCP server 绑定。

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
| `/btw`         | 提问      |
| `/context`     | 上下文状态查看 |
| `/export`      | 导出相关文件  |
| `/memory`      | 记忆管理    |
| `/permissions` | 权限管理    |


