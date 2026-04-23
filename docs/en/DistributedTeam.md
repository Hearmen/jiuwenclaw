# Distributed Team

This guide is for **development and integration testing**: how distributed Team (`team.runtime.mode=distributed` + `pyzmq`) maps to AgentServer / `TeamManager`, where config and code live, and how to run leader and teammate from two separate config roots for end-to-end verification. There is no separate runtime binary; the entry point remains the unified AgentServer.

The main config file is usually `~/.jiuwenclaw/config/config.yaml`. Override the directory with `JIUWENCLAW_CONFIG_DIR` (same as [Configuration](Configuration.md)).

[中文版（Chinese）](../zh/分布式Team.md)

---

## 1. Overview

| Item | Description |
|------|-------------|
| **Mode** | `team.runtime.mode`: `local \| distributed` |
| **Role** | `team.runtime.role`: `leader \| teammate` |
| **Transport** | `team.transport.type`: `inprocess \| pyzmq`; distributed setups typically use `pyzmq` |
| **Entry** | `TeamManager` (`jiuwenclaw/agentserver/team/team_manager.py`): normalizes transport / identity before building `TeamAgentSpec` |
| **Loading** | `load_team_spec_dict()` (`jiuwenclaw/agentserver/team/config_loader.py`): `name` / `display_name` compatibility for leader and `predefined_members` |
| **Sample** | `jiuwenclaw/resources/config.team.distributed.yaml` in the repo (copy into your local config and adjust paths and hosts) |

**Session semantics**: aligned with regular Team—**single active session** per process: creating a Team for a new session tears down other session Teams first. This document does not add a multi-session routing layer for distributed mode.

---

## 2. Config keys you will touch

Typical keys for distributed integration (full template: `config.team.distributed.yaml` in the repo).

| Key | Meaning |
|-----|---------|
| `team.runtime.mode` | Set to `distributed` for distributed semantics |
| `team.runtime.role` | Whether this process is `leader` or `teammate` |
| `team.runtime.member_name` | Recommended on teammate to match `predefined_members` |
| `team.transport.type` | `pyzmq` |
| `team.transport.params` | Leader/teammate hosts and ports, `direct_addr`, `pubsub_*`, `known_peers`, etc. If `pubsub_publish_addr` / `pubsub_subscribe_addr` are incomplete, `TeamManager` can derive them from the topology block (see below) |
| `team.predefined_members` | Remote teammates; each entry needs `member_name` and **`name` or `display_name`** |
| `team.storage` | For multi-process setups, `connection_string` must point to a **shared** DB (e.g. the same sqlite path visible to all nodes) |

---

## 3. Where to look in code

### 3.1 `TeamManager._load_team_spec`

Pipeline: `load_team_spec_dict(session_id)` → **`_normalize_team_identity_fields`** → if distributed, **`_normalize_distributed_transport_fields`** → `TeamAgentSpec.model_validate`.

Distributed mode detection: **`_is_distributed_mode`** (`runtime.mode == distributed` or `transport.type == pyzmq`).

### 3.2 pyzmq field normalization (bootstrap)

When `transport.type == pyzmq` and **`pubsub_publish_addr` / `pubsub_subscribe_addr` are not both set**, `params.leader` / `params.teammate` (and related fields) are used to fill **`direct_addr`, `pubsub_*`, `known_peers` / `bootstrap_peers`, `metadata.pubsub_bind`** so leader/teammate can start without hand-crafted partial snippets.

### 3.3 `config_loader`

- **`_build_leader_spec`**: keeps `name` and `display_name` consistent.
- **`_build_predefined_members`**: requires `member_name` and **`name` or `display_name`**; otherwise the entry is skipped and logged.

---

## 4. Two config directories (recommended layout)

Use **two separate HOME trees** (or two `JIUWENCLAW_CONFIG_DIR` values) for leader and teammate so configs do not overwrite each other.

Placeholders:

- **Leader config dir**: `<LEADER_HOME>/.jiuwenclaw/config`
- **Teammate config dir**: `<TEAMMATE_HOME>/.jiuwenclaw/config`

Both sides must agree on:

- `team.runtime.mode=distributed`
- `team.runtime.role` as `leader` vs `teammate`
- The **same** pyzmq topology under `team.transport` (hosts, ports, `known_peers`, …)
- `team.storage.params.connection_string` pointing at the **same** sqlite file (or equivalent shared storage)

Open firewall ports as needed; replace `127.0.0.1` with real IPs for multi-host setups.

---

## 5. Example startup (three terminals)

Replace `<REPO_ROOT>`, `<LEADER_HOME>`, `<TEAMMATE_HOME>` with paths on your machine.

### 5.1 Teammate (AgentServer only)

```bash
HOME="<TEAMMATE_HOME>" \
GIT_AUTHOR_NAME="teambot" \
GIT_AUTHOR_EMAIL="teambot@example.com" \
GIT_COMMITTER_NAME="teambot" \
GIT_COMMITTER_EMAIL="teambot@example.com" \
AGENT_SERVER_PORT=28193 \
uv run python -m jiuwenclaw.app_agentserver
```

### 5.2 Leader (Gateway + AgentServer)

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

### 5.3 Web UI (optional)

```bash
cd "<REPO_ROOT>/jiuwenclaw/web"
VITE_WS_BASE="ws://localhost:29100" npm run dev -- --host 0.0.0.0 --port 5173
```

If Git user identity is not configured for the workspace, set `GIT_AUTHOR_*` / `GIT_COMMITTER_*` so Git-based tooling does not fail.

---

## 6. Verification prompt (team workflow)

Use a strict prompt in the web UI (or equivalent channel), adapted to your environment:

```text
[Distributed Team integration check]
You MUST run in team mode and complete the steps in order. Do not skip steps. Do not answer the math directly first.
1. Call team.build_team to create the team (leader + teammate_1).
2. Call team.create_task with title "compute 1+1" and assignee teammate_1.
3. Call team.send_message to teammate_1 asking for the result of 1+1 and one short sentence.
4. Wait until teammate_1 completes and responds.
5. Call team.view_task and confirm the task is completed (or equivalent).
6. Have the leader summarize the final answer.
Output format:
- STEP1: <result>
- STEP2: <result>
- STEP3: <result>
- STEP4: <result>
- STEP5: <result>
- FINAL: <final answer>
If any step fails, output FAILED_AT_STEP=<n> and the error.
```

### Success criteria (short)

- UI receives `chat.delta` and eventually `chat.final`.
- Leader logs: Team creation, `team.*` tool usage.
- Teammate logs: participation in session and task coordination.

---

## 7. Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `Address already in use (tcp://0.0.0.0:18555)` | pyzmq bind port in use; free the port or change `direct_port` / topology ports in config. |
| `git commit failed ... Author identity unknown` | Export `GIT_AUTHOR_*` / `GIT_COMMITTER_*` in the startup command. |
| UI idle while backends run | Frontend must use `VITE_WS_BASE` (not `VITE_WS_URL`). |
| Teammate cannot reach leader | Firewall, `known_peers`, replace `127.0.0.1` with real IPs on multi-host. |

---

## 8. Appendix: vs single-machine / inprocess Team

| Aspect | Single-machine / inprocess | Distributed (this guide) |
|--------|----------------------------|---------------------------|
| Entry | Same `TeamManager` | Same entry; behavior split by config |
| Transport | Mostly `inprocess` | `pyzmq`; hosts and ports must be reachable |
| Deployment | Single process | Leader/teammate can be separate processes or hosts |
| Config | Local `team` block suffices | Needs `runtime` + `transport` + shared storage agreement |

For deeper topology evolution, maintain a separate design note alongside this guide; day-to-day work follows **sections 2–7**.
