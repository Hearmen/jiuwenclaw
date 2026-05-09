# Slash Commands Reference

This document categorizes commands by **parsing location**: `TUI Local Parsing` and `Gateway / Agent Parsing`.
For quick reference of current behavior; final implementation follows the code.

---

## Overview: By Parsing Side

### TUI Local Parsing (CLI Built-in)

Executed locally in the terminal UI, not through Gateway control pipeline.

| Command | Description |
|---|---|
| `/clear` | Clear screen |
| `/color` | Adjust TUI color scheme |
| `/copy` | Copy last message |
| `/exit` | Exit |
| `/help` | Show available commands |
| `/theme` | Switch theme |
| `/config` | Modify configuration (currently local, planned to unify with Gateway) |
| `/workspace` | Manage trusted directories (see below) |
| `/teamskills` | TeamSkills Hub publish/delete (`publish`/`delete`) |
| `/export` | Export current conversation to file or clipboard (see below) |
| `/status` | Show jiuwenclaw status overview, usage, config (see below) |
| `/permissions` | Manage tool permissions (`allow`/`ask`/`deny`) |

> Note: `/mode` controlled switching logic is primarily on Gateway side, see "`/mode` and `/switch`" below.

### Gateway / Agent Parsing (Controlled Channel)

Identified by Gateway and forwarded to AgentServer and other backend capabilities.

| Command | Description |
|---|---|
| `/plan` | Switch to planning sub-mode |
| `/resume` | Resume historical session (see below) |
| `/new_session` | Create new session (IM only) |
| `/mode` | Mode switching (supports first-level entry and direct syntax) |
| `/switch` | Switch second-level mode within current mode family |
| `/skills` | Skills management (list, install, import, uninstall, marketplace) (see below) |
| `/model` | Model view, add, switch (see below) |
| `/mcp` | MCP server management (see below) |
| `/diff` | View session changes by turn (see below) |
| `/compact` | Compress current context (see below) |
| `/init` | Project initialization (see below) |
| `/branch` | Create a branch session from current conversation point (see below) |
| `/rewind` | Rewind conversation to before a specific turn (see below) |
| `/memory` | Memory management (see below) |

---

## Key Command Details

### `/workspace` (TUI Trusted Directory Management)

Manages directories AI can access for file read, edit, and execute operations.

#### Subcommands

| Command | Description |
|---|---|
| `/workspace` or `/workspace get` | Show system default workspace and current trusted directories list |
| `/workspace add [path]` | Add trusted directory (defaults to cwd; error if path doesn't exist) |
| `/workspace set <path>` | Reset trusted dirs to single path (confirmation required if dirs exist) |
| `/workspace remove <path>` | Remove specified trusted directory |
| `/workspace clear` | Clear all trusted directories (use default workspace only) |

#### Concepts

- **System default workspace**: Fixed path `~/.jiuwenclaw/agent/jiuwenclaw_workspace`, always available
- **Trusted directories (`trusted_dirs`)**: User-authorized accessible directories, managed by TUI, passed to backend Agent

#### Control Logic

1. **Startup confirmation**: TUI prompts user whether to trust current directory
   - "Trust" → add current directory as trusted
   - "Don't trust" → use default workspace only

2. **Session-level management**: Trusted directories are effective for current CLI session, not persisted to file

3. **Backend passing**: TUI passes `trusted_dirs` via request params; Agent restricts file operations accordingly

4. **Path restriction**: Agent limits file operations within trusted directories; operations outside require user confirmation

5. **Path validation**: `add` and `set` validate path existence; error shown if invalid

#### Aliases

`/workspace_dir`, `/workspace-dir`

### `/mode` and `/switch` (Controlled Channel)

- First-level entry mapping:
  - `/mode agent` -> `agent.plan`
  - `/mode code` -> `code.normal`
  - `/mode team` -> `team`
- Direct syntax:
  - `/mode agent.plan` -> `agent.plan`
  - `/mode agent.fast` -> `agent.fast`
  - `/mode code.plan` -> `code.plan`
  - `/mode code.normal` -> `code.normal`
- Second-level switching:
  - agent family: `/switch plan` <-> `agent.plan`, `/switch fast` <-> `agent.fast`
  - code family: `/switch plan` <-> `code.plan`, `/switch normal` <-> `code.normal`
- Invalid combinations (e.g., `/switch fast` under `code.*`) return: `Invalid command`.
- Note: Standalone `/team` command removed, use `/mode team` instead.

### `/resume`

- `/resume list`: List historical sessions.
- `/resume <conversation_id>`: Resume specified session.

### `/model` (View / Add / Switch Model)

- Usage:
  - `/model` or `/model list`: List switchable models (with current model marker);
  - `/model <name>`: Switch to specified model;
  - `/model add <name> key=value ...`: Add model config (e.g., `model=...`, `provider=...`, `api_base=...`, `api_key=...`).
- Limitation: `video` / `audio` / `vision` cannot be set as default chat model via `/model <name>`, use `/config edit` or `/config set` instead.
- Config write behavior:
  - Adding model writes to `config.yaml` `models.defaults` (compatible with old structure), triggers Agent config reload;
  - Switching model validates config and environment variable placeholders, updates `MODEL_NAME` / `MODEL_PROVIDER` / `API_BASE` / `API_KEY`, writes back to `.env`.
- Secure display: Sensitive fields like `api_key`, `token` are masked.

### `/diff` (Session Change Review)

- Usage: `/diff` (no subcommands).
- Data source: TUI requests Agent diff service via `command.diff`, returns `turns` (change sets per turn) for current `session_id`.
- Display rules:
  - With changes: Shows `Found N turn(s) with file changes` with structured `turns`;
  - Without changes: Shows `No file changes in this session`.
- Scope: For viewing uncommitted per-turn change traces in current session, not a replacement for `git diff` full version control perspective.

### `/compact` (Context Compression)

- Usage: `/compact` (no parameters).
- Function: Trigger context compression,清理对话 history but keep summary in context.
- Data source: TUI requests Agent compression service via `command.compact`.
- Results:
  - `busy`: Compression in progress, retry later;
  - `compressed`: Success, shows before/after token counts and savings ratio;
  - `noop`: No compression needed, context already optimal.

### `/init` (Project Initialization)

- Usage: `/init` (no parameters).
- Function: Initialize project AI collaboration config, generates `JIUWENCLAW.md` and optionally `JIUWENCLAW.local.md`.
- Scope: Only runs in `code` mode.
- Flow:
  1. Select scope: `Team-shared` (JIUWENCLAW.md), `Personal` (JIUWENCLAW.local.md), or `Both`.
  2. Detect existing configs: Auto-detect `CLAUDE.md`, `.cursorrules`, `copilot-instructions.md` etc.
  3. Generate configs: Create project config files based on selection.
- Auto mode switch: If in `code.plan`, auto-switches to `code.normal` for write permission.

### `/mcp` (MCP Server Management)

- Usage:
  - `/mcp list`: List all MCP servers (name, transport, enabled status);
  - `/mcp show [name]`: Show MCP config; without `name` shows enabled items, with `name` shows one server detail;
  - `/mcp add --name <name> --transport <stdio|sse> ...`: Add a new MCP server;
  - `/mcp update --name <name> ...`: Update MCP server config (transport / params / enabled status);
  - `/mcp enable <name>`: Enable a specific MCP server;
  - `/mcp disable <name>`: Disable a specific MCP server;
  - `/mcp remove <name>`: Remove a specific MCP server.
- Transport parameters:
  - `stdio`: requires `--command`; optional `--args`, `--cwd`, `--env`;
  - `sse`: requires `--url`; optional `--headers`, `--timeout_s`.
- Examples:
  - `/mcp list`
  - `/mcp show`
  - `/mcp show playwright`
  - `/mcp add --name playwright --transport stdio --command python --args "server.py --transport stdio"`
  - `/mcp update --name playwright --transport sse --url http://127.0.0.1:9000/sse --headers "Authorization=Bearer xxx"`
  - `/mcp add --name local-sse --transport sse --url http://127.0.0.1:9000/sse`
  - `/mcp disable playwright`
  - `/mcp remove local-sse`
- Config and effect:
  - Changes are written to `config.yaml` under `mcp.servers`;
  - After write, Agent config reload is triggered, and runtime MCP server bindings are synced accordingly.

### `/branch` (Branch Session)

- Usage: `/branch [name]`.
- Alias: `/fork`.
- Function: Create a branch session from the current conversation state, copying the current conversation history.
- Constraints:
  - Rejected when the session is busy (`session is busy`);
  - Rejected when the current session has no conversation records.
- Behavior:
  1. Generate a new `session_id` and send `session.fork` RPC to the backend (carrying `source_session_id`, `target_session_id`, and optional title).
  2. TUI automatically switches to the new branch session, clears the current transcript, and restores the branch history.
  3. Prompts the user that they are now in the new branch, and informs them they can use `/resume <original_session_id>` to return to the original session.
- Examples:
  - `/branch` — Create an untitled branch
  - `/branch fix-login-bug` — Create a branch named `fix-login-bug`

### `/rewind` (Rewind Conversation)

- Usage: `/rewind [turn_number]`.
- Alias: `/checkpoint`.
- Function: Rewind the current session to before a specified turn, supporting conversation-only, code-only, or both.
- Constraints:
  - Rejected when the session is busy (`session is busy`);
  - Rejected when there are no conversation turns.
- Interactive flow:
  1. Without parameters, displays a list of all conversation turns (with timestamps and file change statistics) for the user to select the target turn.
  2. After selecting, displays restore options:
     - **Restore conversation and code** — Truncate conversation and restore files to their prior state;
     - **Restore conversation only** — Only truncate conversation, files remain unchanged;
     - **Restore code only** — Only restore files, conversation remains unchanged (shown only when the target turn has file changes);
     - **Cancel** — Abort the operation.
  3. Calls the corresponding backend RPC based on selection:
     - `both` → `session.rewind_and_restore`
     - `conversation` → `session.rewind`
     - `code` → `session.restore_files`
- After rewind: TUI clears the transcript and reloads history; if the rewinded content contains user input, it is automatically filled into the input box.
- Limitation: Rewinding does not affect files edited manually or via bash commands.
- Examples:
  - `/rewind` — Interactive turn selection and restore mode confirmation
  - `/rewind 2` — Directly rewind to before turn 2

### `/memory` (Memory Management)

- Alias: `/mem`.
- Function: View and manage memory system status, memory files, toggle settings, and directory paths.
- Subcommands:

| Command | Description |
|---|---|
| `/memory` or `/memory edit` | Interactively select and edit a memory file (lists available files when no path is given) |
| `/memory list` | List all memory files (with size, line count, modification time) |
| `/memory edit <path>` | Open the specified memory file for editing (via `$EDITOR`) |
| `/memory status` | Show detailed memory system status |
| `/memory toggle [key]` | Toggle memory system switches (lists togglable items when no key is given) |
| `/memory open` | Show memory system directory paths |

- `status` display contents:
  - Current mode, storage engine, enabled status, proactive status, forbidden filter status;
  - Index status (FTS5, Vector, Cache), file count, chunk count;
  - Statistics for Project Memory, Coding Memory, Auto Memory, and External Memory.
- `toggle` available keys:
  - `memory_enabled` — Master memory switch;
  - `memory_proactive` — Proactive memory switch;
  - `memory_forbidden_enabled` — Forbidden filter switch.
  - After toggling, a prompt is shown if a session restart is required for the change to take effect.
- Examples:
  - `/memory` — Interactively edit a memory file
  - `/memory list` — List memory files
  - `/memory edit memory/MEMORY.md` — Edit a specific memory file
  - `/memory status` — View detailed status
  - `/memory toggle memory_enabled` — Toggle the master memory switch
  - `/memory open` — View memory directory paths

### `/skills` (Skills Management)

Manage skills lifecycle: listing, installing, importing, uninstalling, and marketplace source management.

#### Subcommands

| Command | Description |
|---|---|
| `/skills` or `/skills list` | List skills (grouped: Installed / Available to install) |
| `/skills install <skill>` or `/skills install <skill@marketplace>` | Install a skill: builtin skills accept bare name, marketplace skills use `<name>@<marketplace>` format |
| `/skills import <path>` | Import a custom skill from a local path or remote URL |
| `/skills uninstall <name>` | Uninstall a skill by name |
| `/skills marketplace` or `/skills marketplace list` | List marketplace sources (name, URL, enabled status, last updated) |
| `/skills marketplace add <name> <url>` | Add a new marketplace source |
| `/skills marketplace remove <name>` | Remove a marketplace source (also clears its cache) |
| `/skills marketplace toggle <name> <on or off>` | Enable or disable a marketplace source (`on`/`true`/`1` = enable, otherwise disable) |
| `/skills use <skill_name>, <query>` | Execute a query using a specific skill |

#### Concepts

- **Skill**: An extension capability that can be installed from marketplace sources, builtin directory, or local paths, providing additional functionality to the agent.
- **Builtin skill**: A preset skill shipped with the software. Install using bare skill name (e.g., `/skills install advanced-daily-report`); no marketplace source needed.
- **Marketplace source**: A remote repository (typically a Git URL) that hosts available skills. Each source has a name, URL, and enabled/disabled state.
- **Spec**: The install identifier format `<skill>@<marketplace>` used when installing from a marketplace; for builtin skills, omit `@` and the system auto-detects as `@builtin`.
- **Import**: Copy a local directory (must contain `SKILL.md`) or remote archive URL into the workspace as a custom skill.
- **Install location**: The directory where a skill is stored after installation (`~/.jiuwenclaw/agent/jiuwenclaw_workspace/skills/`).
- **Source tag**: Each skill in the list is tagged with its source: `[builtin]` = builtin, `[local]` = imported, `[project]` or marketplace name = other.

#### Grouped List Display

`/skills list` returns skills in two groups:

1. **Installed**: Skills already in the user's skills directory, ready to use.
2. **Available to install**: Builtin skills not yet installed, plus marketplace skills available for installation. Use `/skills install` or `/skills import` first.

#### IM vs TUI Differences

Both ultimately request `skills.list`, but trigger methods and display differ.

| Side | Trigger Method | Behavior |
|---|---|---|
| IM (Feishu etc. controlled channel) | Exact match `/skills list` (whitespace normalized first) | Gateway intercepts control message and requests `skills.list`, results shown as IM notification/card; standalone `/skills` doesn't go through this control path. |
| TUI (CLI built-in) | Input `/skills` | Locally executes built-in command and calls `skills.list`, displays as grouped list view in session (titles `Installed Skills` and `Available Skills`); shows `No installed skills` when empty. |

For other subcommands (`/skills install`, `/skills import`, `/skills uninstall`, `/skills marketplace add/remove/toggle`, `/skills use`), Gateway does **not** intercept them — on the IM side they are treated as regular chat messages. These subcommands are only functional on the TUI (CLI built-in) and Web UI paths, where they send RPC requests directly to AgentServer.

#### Notes

- **Timeout**: `install`, `import`, `uninstall`, and `marketplace toggle` requests have a 120-second timeout on the TUI side; other subcommands have no explicit timeout.
- **Builtin auto-detection**: When installing with `/skills install <skill>` (no `@`), the system checks if it matches a builtin skill and redirects to the builtin install flow; if not, a format hint is returned.
- **Cache cleanup**: `marketplace remove` sends `{ name, remove_cache: true }` to also clear the local cache for that source.
- **Auto-refresh**: `marketplace add`, `marketplace remove`, and `marketplace toggle` automatically re-list marketplace sources after a successful operation.
- **Offline handling**: `/skills use` checks connection status; if offline, shows `offline: waiting for reconnect before sending /skills use request`.

#### Examples

- `/skills` — List skills (grouped: Installed / Available)
- `/skills list` — List skills (explicit subcommand)
- `/skills install advanced-daily-report` — Install a builtin skill (bare name auto-detect)
- `/skills install advanced-daily-report@builtin` — Install a builtin skill (explicit format)
- `/skills install my-skill@marketplace` — Install a skill from marketplace
- `/skills import /path/to/my-skill` — Import a skill from local directory
- `/skills import https://example.com/skill.zip` — Import a skill from remote URL
- `/skills uninstall my-skill` — Uninstall a skill
- `/skills marketplace list` — List marketplace sources
- `/skills marketplace add community https://github.com/user/skills-repo` — Add a marketplace source named "community"
- `/skills marketplace remove community` — Remove the "community" marketplace source
- `/skills marketplace toggle community on` — Enable the "community" marketplace source
- `/skills marketplace toggle community off` — Disable the "community" marketplace source
- `/skills use my-skill, Code and execute a Hello World program.` — Use a skill to execute a query

### `/export` (Export Conversation)

Export the current conversation to a file or clipboard.

#### Usage

- `/export` — Copy conversation to clipboard (no filename argument)
- `/export <filename>` — Save conversation to a `.txt` file in workspace directory

#### Subcommands

| Command | Description |
|---|---|
| `/export` | Copy entire conversation to clipboard; if clipboard unavailable, prompt to specify a filename |
| `/export <filename>` | Write conversation to `filename.txt` in workspace directory; if filename lacks `.txt` extension, it is automatically appended |

#### Output Format

The exported text renders each conversation entry with a timestamp and role prefix:

- `[User] <timestamp>` — User input
- `[Assistant] <timestamp>` — Assistant response
- `[Thinking] <timestamp>` — Internal reasoning trace
- `[Tools] <timestamp>` — Tool calls with name, summary, and truncated result (max 500 chars)
- `[System] / [Error] / [Info] <timestamp>` — System messages
- `[Diff] <timestamp>` — Per-turn file change summary

#### Tab Completion

When typing `/export ` and pressing Tab, auto-generated filename suggestions appear:

- `<timestamp>-<sanitized-first-prompt>.txt` — Based on the first user message (truncated to 50 chars, sanitized)
- `conversation-<timestamp>.txt` — Generic timestamped name

Timestamp format: `YYYY-MM-DD-HHmmss`.

#### Behavior Details

- **Clipboard fallback**: If no filename is given and clipboard is unavailable, an error message prompts the user to specify a filename instead.
- **Filename normalization**: Any extension is replaced with `.txt`; e.g., `/export my-chat.json` becomes `my-chat.txt`.
- **Write location**: Files are saved to `ctx.getWorkspaceDir()` (or `process.cwd()` as fallback).

#### Examples

- `/export` — Copy conversation to clipboard
- `/export my-chat` — Save to `my-chat.txt` in workspace
- `/export 2026-05-09-debug-session.txt` — Save with explicit timestamp name

### `/status` (Show Status)

Display jiuwenclaw runtime status: overview, usage statistics, or config editor.

#### Usage

- `/status` or `/status overview` — Show core identity, model/API info, MCP servers, and config sources
- `/status usage` — Show session token usage statistics
- `/status config` — Enter interactive config editor

#### Subcommands

| Command | Description |
|---|---|
| `/status` | Show full status overview (version, session, model, connection, MCP servers, config) |
| `/status overview` | Same as `/status` — explicit overview subcommand |
| `/status usage` | Show session token usage (input, output, total, per-model breakdown) |
| `/status config` | Enter interactive config editor (same as `/config edit`) |

#### Overview Display Sections

When `/status` is run, four key-value panels are displayed:

1. **Core identity**: version, session ID, session name (or prompt to `/rename`), cwd, current mode
2. **Model & API**: model name, provider, API base URL, connection status
3. **MCP servers**: each server's name, transport type, and enabled/disabled state
4. **Config sources**: config file path and all settings source paths

#### Usage Display

`/status usage` shows token consumption for the current session:

- Total input tokens, output tokens, and total tokens
- Per-model breakdown: model name, token count, input/output split

#### Interactive Mode

If the TUI provides an interactive StatusView (`ctx.enterStatusView`), `/status` opens the full status UI with tabs. The subcommand argument selects the initial tab:

- `/status` → opens on overview tab
- `/status usage` → opens on usage tab
- `/status config` → opens on config tab

If StatusView is unavailable, the command falls back to inline key-value display.

#### Data Sources

- Overview data: `command.status` RPC request to AgentServer
- Usage data: `ctx.getUsageSummary()` from local session tracking
- Config data: `config.get` RPC request to AgentServer

#### Examples

- `/status` — Show full overview
- `/status overview` — Show overview (explicit)
- `/status usage` — Show token usage
- `/status config` — Open config editor

---

## Planned Features

| Command | Description |
|---|---|
| `/btw` | Ask question |
| `/context` | Context status view |
| `/memory` | Memory management |
| `/export` | Export related files |
| `/permissions` | Permission management |