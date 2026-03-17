# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""EvolutionStore - Pure IO layer for skill evolution data."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from jiuwenclaw.evolution.schema import (
    EvolutionChange,
    EvolutionEntry,
    EvolutionFile,
)
from jiuwenclaw.utils import logger

_EVOLUTION_FILENAME = "evolutions.json"


class EvolutionStore:
    """Handles all file-system IO for the evolution system.

    Responsibilities:
      - Skill directory scanning and SKILL.md reading
      - evolutions.json read/write
      - Solidification (writing pending entries into SKILL.md)
      - Evolution summary generation for system prompts
    """

    def __init__(self, skills_base_dir: str) -> None:
        self._base = Path(skills_base_dir)

    @property
    def base_dir(self) -> Path:
        return self._base

    # ------------------------------------------------------------------
    # File-system queries
    # ------------------------------------------------------------------

    def list_skill_names(self) -> List[str]:
        """List all skill directory names under base dir."""
        if not self._base.exists():
            return []
        return [
            d.name
            for d in self._base.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]

    def skill_exists(self, name: str) -> bool:
        return (self._base / name).is_dir()

    def read_skill_content(self, name: str) -> str:
        """Read SKILL.md raw content for a skill."""
        skill_dir = self._base / name
        md_path = self._find_skill_md(skill_dir)
        if md_path is None:
            return ""
        try:
            return md_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[EvolutionStore] failed to read %s: %s", md_path, exc)
            return ""

    # ------------------------------------------------------------------
    # evolutions.json read/write
    # ------------------------------------------------------------------

    def load_evolution_file(self, name: str) -> EvolutionFile:
        skill_dir = self._base / name
        evo_path = skill_dir / _EVOLUTION_FILENAME
        if evo_path.exists():
            try:
                data = json.loads(evo_path.read_text(encoding="utf-8"))
                return EvolutionFile.from_dict(data)
            except Exception as exc:
                logger.warning("[EvolutionStore] read evolutions.json failed: %s", exc)
        return EvolutionFile.empty(skill_id=name)

    def get_pending_entries(self, name: str) -> List[EvolutionEntry]:
        return self.load_evolution_file(name).pending_entries

    def append_entry(self, name: str, entry: EvolutionEntry) -> None:
        """Append an evolution entry to evolutions.json.

        If ``entry.change.merge_target`` is set, replaces the target entry
        instead of appending.
        """
        evo_file = self.load_evolution_file(name)
        merge_target = getattr(entry.change, "merge_target", None)

        if merge_target:
            replaced = False
            for i, existing in enumerate(evo_file.entries):
                if existing.id == merge_target:
                    evo_file.entries[i] = entry
                    replaced = True
                    logger.info(
                        "[EvolutionStore] merged entry %s replacing %s",
                        entry.id,
                        merge_target,
                    )
                    break
            if not replaced:
                evo_file.entries.append(entry)
        else:
            evo_file.entries.append(entry)

        evo_file.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._save_evolution_file(name, evo_file)
        logger.info(
            "[EvolutionStore] wrote %s/evolutions.json (id=%s)", name, entry.id
        )

    # ------------------------------------------------------------------
    # Solidification
    # ------------------------------------------------------------------

    def solidify(self, name: str) -> int:
        """Write pending entries into SKILL.md, mark them as applied.

        Returns:
            Number of entries solidified.
        """
        skill_dir = self._base / name
        evo_file = self.load_evolution_file(name)
        pending = evo_file.pending_entries
        if not pending:
            return 0

        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            logger.warning("[EvolutionStore] solidify: SKILL.md not found (skill=%s)", name)
            return 0

        content = skill_md_path.read_text(encoding="utf-8")
        for entry in pending:
            content = self._inject_section(content, entry.change)
            entry.applied = True

        skill_md_path.write_text(content, encoding="utf-8")
        evo_file.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._save_evolution_file(name, evo_file)
        logger.info("[EvolutionStore] solidified %d entries (skill=%s)", len(pending), name)
        return len(pending)

    # ------------------------------------------------------------------
    # Summaries (for system prompt injection)
    # ------------------------------------------------------------------

    def get_evolution_summary(self, name: str) -> str:
        """Return single skill's pending evolution summary as Markdown."""
        pending = self.get_pending_entries(name)
        if not pending:
            return ""
        lines = [f"\n\n### Skill '{name}' 演进经验（自动注入，待固化）\n"]
        for entry in pending:
            lines.append(f"- **[{entry.change.section}]** {entry.change.content}")
        return "\n".join(lines)

    def get_all_evolution_summaries(self, names: List[str]) -> str:
        """Aggregate pending evolution summaries for multiple skills."""
        parts: List[str] = []
        for name in names:
            summary = self.get_evolution_summary(name)
            if summary:
                parts.append(summary)
        return "\n".join(parts)

    def list_pending_summary(self, names: List[str]) -> str:
        """Return a human-readable pending summary for multiple skills."""
        lines: List[str] = []
        count = 0
        for name in names:
            pending = self.get_pending_entries(name)
            if pending:
                count += 1
                lines.append(f"{count}. **{name}** - 共 {len(pending)} 条 pending 经验")
                for e in pending:
                    content = e.change.content
                    title = content.split("\n")[0] if "\n" in content else content[:50]
                    lines.append(f"   - **{title}**: ")
                    if "\n" in content:
                        body_lines = content.split("\n")[1:]
                        if body_lines:
                            summary = " ".join(
                                ln.strip().lstrip("- ") for ln in body_lines if ln.strip()
                            )
                            lines.append(f"    {summary[:100].replace('**', '')}")
                lines.append("")

        if not lines:
            return "当前所有 Skill 暂无演进信息。"
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_evolution_file(self, name: str, evo_file: EvolutionFile) -> None:
        skill_dir = self._base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        evo_path = skill_dir / _EVOLUTION_FILENAME
        try:
            evo_path.write_text(
                json.dumps(evo_file.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("[EvolutionStore] write evolutions.json failed: %s", exc)

    @staticmethod
    def _find_skill_md(skill_dir: Path) -> Optional[Path]:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md
        md_files = list(skill_dir.glob("*.md"))
        return md_files[0] if md_files else None

    @staticmethod
    def _inject_section(content: str, change: EvolutionChange) -> str:
        """Append change.content to the corresponding section in SKILL.md."""
        section = change.section
        addition = f"\n{change.content}\n"
        header_pattern = re.compile(
            rf"(## {re.escape(section)}.*?)(\n## |\Z)", re.DOTALL
        )
        m = header_pattern.search(content)
        if m:
            insert_pos = m.start(2)
            content = content[:insert_pos] + addition + content[insert_pos:]
        else:
            content = content.rstrip() + f"\n\n## {section}\n{change.content}\n"
        return content
