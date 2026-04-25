# 分布式 Team

本文面向 **开发与联调**：说明分布式 Team（`team.runtime.mode=distributed` + `pyzmq`）在现有 AgentServer / TeamManager 中的配置入口、代码落点，以及如何在本机或双目录起 leader/teammate 做闭环验证。不要求单独的运行时二进制；业务入口仍为统一 AgentServer。

配置主文件一般为 `~/.jiuwenclaw/config/config.yaml`；可通过环境变量 `JIUWENCLAW_CONFIG_DIR` 指向其它目录（与 [配置说明](配置信息.md) 一致）。

English: [Distributed Team](../en/DistributedTeam.md)

---

## 1. 总览

| 项目 | 说明 |
|------|------|
| **模式开关** | `team.runtime.mode`: `local \| distributed` |
| **进程角色** | `team.runtime.role`: `leader \| teammate` |
| **传输** | `team.transport.type`: `inprocess \| pyzmq`；分布式联调通常用 `pyzmq` |
| **入口类** | `TeamManager`（`jiuwenclaw/agentserver/team/team_manager.py`）：构建 `TeamAgentSpec` 前会做 transport/身份字段归一化 |
| **配置装载** | `load_team_spec_dict()`（`jiuwenclaw/agentserver/team/config_loader.py`）：leader / `predefined_members` 的 name 与 display_name 兼容 |
| **样例** | 仓库内 `jiuwenclaw/resources/config.team.distributed.yaml`（通用）以及 `config.team.distributed.leader.yaml` / `config.team.distributed.teammate.yaml`（当前分角色模板） |

**会话语义**：与原 Team 一致倾向 **单活 session**——新建 session 的 Team 前会清理其它 session 的 Team 资源；分布式下不在本文档引入多 session 并发路由层。

---

## 2. 你需要关心的配置键

以下为分布式联调最常改动的键（完整模板见仓库 `config.team.distributed.yaml`，分角色模板见 `config.team.distributed.leader.yaml` / `config.team.distributed.teammate.yaml`）。

| 键 | 含义 |
|----|------|
| `team.runtime.mode` | `distributed` 启用分布式语义 |
| `team.runtime.role` | 本进程是 `leader` 还是 `teammate` |
| `team.runtime.member_name` | teammate 侧建议显式指定（与 `predefined_members` 对齐） |
| `team.transport.type` | `pyzmq` |
| `team.transport.params` | `leader` / `teammate` 主机与端口、`direct_addr`、`pubsub_*`、`known_peers` 等；若未填全 pubsub 地址，`TeamManager` 可按拓扑块推导（见下一节） |
| `team.predefined_members` | 声明远端 teammate；成员需同时具备 `member_name` 与 `name` 或 `display_name` |
| `team.storage` | 多进程场景下 `connection_string` 需指向 **各方可见的同一 DB**（如共享路径下的 sqlite） |

---

## 3. 代码落点（改 bug / 跟逻辑时从这里进）

### 3.1 `TeamManager._load_team_spec`

流程：`load_team_spec_dict(session_id)` → **`_normalize_team_identity_fields`** → 若判定为分布式则 **`_normalize_distributed_transport_fields`** → `TeamAgentSpec.model_validate`。

分布式判定：见 **`_is_distributed_mode`**（`runtime.mode == distributed` 或 `transport.type == pyzmq`）。

### 3.2 pyzmq 字段归一化（bootstrap 语义）

当 `transport.type == pyzmq` 且 **`pubsub_publish_addr` / `pubsub_subscribe_addr` 尚未同时存在** 时，会根据 `params.leader` / `params.teammate` 等拓扑信息补全 **`direct_addr`、`pubsub_*`、`known_peers` / `bootstrap_peers`、`metadata.pubsub_bind`**，避免仅靠手写片段导致 leader/teammate 起不来的情况。

### 3.3 `config_loader`

- **`_build_leader_spec`**：补齐 `name` 与 `display_name` 的兼容。
- **`_build_predefined_members`**：必须有 `member_name`，且必须有 **`name` 或 `display_name`**，否则跳过并打日志。

### 3.4 当前分支的控制面 / 数据面实现（重点）

当前实现已按“控制面建连、数据面跑业务”分层：

- **控制面（Control Plane）**：
  - leader 在 `spawn_member` 后通过 direct ZMQ 发送 `jiuwen.remote_teammate_bootstrap.direct`。
  - teammate 监听 `bootstrap_direct_addr` 接收 bootstrap，应用 leader 路由并完成接管。
  - ACK 使用 direct 传输层确认（不再依赖 DB ACK 消息链路）。
- **数据面（Data Plane）**：
  - 任务创建、认领、完成、普通团队消息仍走 team 业务链路（共享存储 + team runtime）。
- **兜底策略（当前）**：
  - leader 侧 direct bootstrap 发送失败后，**不再 fallback 到 `team_message`**。
  - teammate 侧 bootstrap 接收也**不再使用 DB 轮询兜底**。

---

## 4. 当前推荐配置方式（模板）

建议优先使用仓库内分角色模板：

- `jiuwenclaw/resources/config.team.distributed.leader.yaml`
- `jiuwenclaw/resources/config.team.distributed.teammate.yaml`

用法（建议）：

1. 复制对应模板到各自配置目录（如 `<LEADER_HOME>/.jiuwenclaw/config/config.yaml` 和 `<TEAMMATE_HOME>/.jiuwenclaw/config/config.yaml`）。
2. 按环境替换以下字段：
   - `team.transport.params` 中的主机/端口（多机时把 `127.0.0.1` 改成真实地址）。
   - `team.storage.params.connection_string`（leader 与 teammate 必须一致）。
   - teammate 的 `team.runtime.member_name`（与 leader 端 peer 配置一致）。

最小可用示例（复制模板到当前运行目录）：

```bash
# leader
mkdir -p "<LEADER_HOME>/.jiuwenclaw/config"
cp "<REPO_ROOT>/jiuwenclaw/resources/config.team.distributed.leader.yaml" \
  "<LEADER_HOME>/.jiuwenclaw/config/config.yaml"

# teammate
mkdir -p "<TEAMMATE_HOME>/.jiuwenclaw/config"
cp "<REPO_ROOT>/jiuwenclaw/resources/config.team.distributed.teammate.yaml" \
  "<TEAMMATE_HOME>/.jiuwenclaw/config/config.yaml"
```

---

## 5. 本地双目录联调（推荐布局）

用 **两个独立 HOME**（或两套 `JIUWENCLAW_CONFIG_DIR`）分别模拟 leader 与 teammate，避免配置互相覆盖。

下文用占位符：

- **Leader 配置目录**：`<LEADER_HOME>/.jiuwenclaw/config`
- **Teammate 配置目录**：`<TEAMMATE_HOME>/.jiuwenclaw/config`

两侧需约定一致：

- `team.runtime.mode=distributed`
- `team.runtime.role` 分别为 `leader` / `teammate`
- `team.transport` 使用 **同一套** pyzmq 拓扑（主机、端口、`known_peers` 等）
- `team.storage.params.connection_string` 指向 **同一 sqlite 文件路径**（或等价的共享存储）

端口与防火墙需保证 leader/teammate 机器互通（多机时把示例中的 `127.0.0.1` 换成真实 IP）。

---

## 6. 启动命令示例（三个终端）

以下路径请替换为你的本机 `<REPO_ROOT>`、`<LEADER_HOME>`、`<TEAMMATE_HOME>`。

### 5.1 Teammate（仅 AgentServer）

```bash
HOME="<TEAMMATE_HOME>" \
GIT_AUTHOR_NAME="teambot" \
GIT_AUTHOR_EMAIL="teambot@example.com" \
GIT_COMMITTER_NAME="teambot" \
GIT_COMMITTER_EMAIL="teambot@example.com" \
AGENT_SERVER_PORT=28193 \
uv run python -m jiuwenclaw.app_agentserver
```

### 5.2 Leader（Gateway + AgentServer）

```bash
HOME="<LEADER_HOME>" \
GIT_AUTHOR_NAME="teambot" \
GIT_AUTHOR_EMAIL="teambot@example.com" \
GIT_COMMITTER_NAME="teambot" \
GIT_COMMITTER_EMAIL="teambot@example.com" \
AGENT_SERVER_PORT=28192 \
GATEWAY_PORT=29101 \
WEB_PORT=29100 \
uv run python -m jiuwenclaw.app
```

### 5.3 Web 前端（可选）

```bash
cd "<REPO_ROOT>/jiuwenclaw/web"
VITE_WS_BASE="ws://localhost:29100" npm run dev -- --host 0.0.0.0 --port 5173
```

若 workspace 未配置 Git 用户信息，启动前建议带上 `GIT_AUTHOR_*`，否则涉及 git 的工具链可能报错。

---

## 7. 验证 Prompt（团队协作闭环）

在前端（或等价通道）可用下列指令做强约束联调（可按环境改写）：

```text
【分布式Team联调验证指令】
你必须以 team 模式执行，并严格按顺序完成以下步骤，不允许跳过，不允许直接给最终答案：
1. 调用 team.build_team 创建团队（leader + teammate_1）。
2. 调用 team.create_task 创建任务，标题为“计算1+1”，并将 assignee 指定为 teammate_1。
3. 调用 team.send_message 给 teammate_1，要求其返回“1+1”的计算结果与一句简短说明。
4. 等待 teammate_1 完成任务并回传消息。
5. 调用 team.view_task 查看该任务状态，确认是 completed（或等效完成态）。
6. 最后由 leader 汇总输出最终答案。
输出格式必须为：
- STEP1: <执行结果>
- STEP2: <执行结果>
- STEP3: <执行结果>
- STEP4: <执行结果>
- STEP5: <执行结果>
- FINAL: <最终答案>
如果任一步失败，请明确输出 FAILED_AT_STEP=<步骤号> 和错误原因。
```

### 成功判定（简要）

- 前端能持续收到 `chat.delta`，并最终出现 `chat.final`。
- Leader 日志：可见 Team 创建、`team.*` 工具调用等。
- Teammate 日志：可见参与会话与任务协同。

---

## 8. 常见问题排查

| 现象 | 处理方向 |
|------|----------|
| `Address already in use (tcp://0.0.0.0:18555)` | pyzmq 绑定端口被占用；释放端口或改配置中的 `direct_port` / 拓扑端口。 |
| `git commit failed ... Author identity unknown` | 为启动命令补充 `GIT_AUTHOR_*` / `GIT_COMMITTER_*`。 |
| 前端无响应但后端已启动 | 确认前端使用 `VITE_WS_BASE`（而不是误用 `VITE_WS_URL`）。 |
| teammate 连不上 leader | 检查防火墙、`known_peers` / 主机 IP 是否仍为 `127.0.0.1`（多机需改为真实地址）。 |

---

## 9. 附录：与原单机 Team 的差异速查

| 维度 | 单机 / inprocess 倾向 | 分布式（本指南范围） |
|------|------------------------|----------------------|
| 入口 | 同一 `TeamManager` | 同一入口，按配置分流 |
| 传输 | `inprocess` 为主 | `pyzmq`，需地址与端口可达 |
| 部署 | 单进程 | leader / teammate 可分进程、可多机 |
| 配置 | 本地 `team` 即可 | 需 `runtime` + `transport` + 共享 storage 约定 |

更完整的拓扑与演进若单独成文，可与本指南并列维护；日常开发以 **第 2～7 节** 为准。
