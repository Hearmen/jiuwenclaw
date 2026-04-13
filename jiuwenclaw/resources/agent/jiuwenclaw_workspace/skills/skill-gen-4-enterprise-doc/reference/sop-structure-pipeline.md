# SOP → structured JSON (single path)

There is **one** supported way to turn SOP plain text into a full **`SOPStructure`** (rich `steps`, `knowledge_items`, `sections`, `sop_type`, decision points, exceptions, references, etc.):

Call **`skill_gen.sop_parser.parse_sop_file`** or **`parse_sop_raw_text`** and pass a required **`invoke_llm_json`** callable. The implementation chooses single-shot vs chunked map-reduce and optional reconcile using **`parse_options`** (`sop_parse_mode`, context limits, chunk sizes—see the functions’ docstrings in `scripts/skill_gen/sop_parser.py`).

**Authoritative extraction prompts** (what the model must return, field semantics, step vs knowledge rules) live **only** in:

- `scripts/skill_gen/sop_parser.py` — full-document extraction template  
- `scripts/skill_gen/sop_chunk_merge.py` — per-chunk and reconcile templates  

Do not maintain a second, competing extraction spec elsewhere.

**Shell helpers:** `scripts/skill_generator_cli.py sop-text` outputs **plain text** extracted from a file (character count or full text). It does **not** emit `SOPStructure` JSON. For HTTP(S) or WeChat pages, use **`url-fetch`**, then run the same **`parse_sop_raw_text`** on the fetched body with **`invoke_llm_json`**.

**After `SOPStructure` exists:** Write the target skill’s **`SKILL.md`** (user inputs, processing steps, deliverables, triggers) solely per **[generator-worker-spec.md](./generator-worker-spec.md)** under **`get_agent_workspace_dir() / "skills-draft" / <skill_name>`** (or the host’s equivalent), then in the **same workflow** call **`skills.import_local`** with the **absolute** draft directory path so the package is copied to **`get_agent_skills_dir() / <skill_name>`** and is immediately loadable ([operator-playbook.md](./operator-playbook.md) **Canonical flow**). Use **`force: true`** when overwriting an existing installed skill.
