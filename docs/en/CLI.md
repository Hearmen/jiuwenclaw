## CLI / channel control commands

JiuwenClaw supports **special prefix commands** to control sessions and modes. Common ones:

- `/new_session`: start a new `session_id` for the current channel
- `/mode plan` or `/mode agent`: switch the channel’s working mode

These are handled in the Gateway **`MessageHandler`** and **are not** sent to the agent.

---

### 1. `/new_session` — new session id

**Behavior**

- For supported channels (`feishu` / `xiaoyi` / `dingtalk`), generates a new `session_id`, e.g.:  
  - `feishu_<ms hex>_<random hex>`
  - `xiaoyi_<ms hex>_<random hex>`
  - `dingtalk_<ms hex>_<random hex>`
- Later messages on that channel use this id, so a new folder appears under `workspace/session/`.

**Usage**

Send in the channel (Feishu / Xiaoyi / DingTalk):

  ```text
  /new_session
  ```
![](../assets/images/命令行解析.jpg)

The Gateway will:

  1. Intercept (not forwarded to the agent)
  2. Generate a new `session_id` for that `channel_id`
  3. Reply with a system message, e.g.  
     `session_id updated to feishu_17f2b4b32e0_ab12cd`

**Notes**

- `/new_session` only changes **future** message binding; the directory is created when the session is actually used (todo, files, etc.).

---

### 2. `/mode` — channel mode (`plan` / `agent`)

**Behavior**

- Sets a logical **mode** for the channel:
  - `plan`: planning, explanation, decomposition (default)
  - `agent`: more hands-on execution
- Mode is passed in `params["mode"]` for prompt construction.

**Usage**

  ```text
  /mode plan
  ```

  or

  ```text
  /mode agent
  ```

The Gateway will:

  1. Treat as control, not forward to the agent
  2. Update `ChannelControlState.mode`
  3. Reply e.g. `mode updated to agent`

**Scope**

- Stored **per channel** (`channel_id` → `mode`). All later messages on that channel use the current mode.
- Initial value can come from `default_mode` in config; `MessageHandler` reads it on startup.

