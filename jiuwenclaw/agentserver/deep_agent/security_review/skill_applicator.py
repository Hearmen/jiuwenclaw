# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Apply approved security review skill candidates to the user skills directory."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_agent_skills_dir


class SecuritySkillApplicationError(ValueError):
    """Raised when an approved security skill candidate cannot be applied."""


def security_skill_candidate_to_skill_spec(candidate: dict[str, Any]) -> dict[str, str]:
    if candidate.get("type") != "security_skill":
        raise SecuritySkillApplicationError("candidate type must be security_skill")
    if candidate.get("requires_approval") is not True:
        raise SecuritySkillApplicationError("candidate requires approval")

    title = str(candidate.get("title") or "").strip()
    if not title:
        raise SecuritySkillApplicationError("title must be non-empty")

    problem = str(candidate.get("problem") or "").strip()
    if not problem:
        raise SecuritySkillApplicationError("problem must be non-empty")

    scope = str(candidate.get("suggested_skill_scope") or "").strip()
    if not scope:
        raise SecuritySkillApplicationError("suggested_skill_scope must be non-empty")

    evidence = _coerce_non_empty_list(candidate.get("evidence"), "evidence")
    recommended_response = str(
        candidate.get("recommended_response")
        or candidate.get("response")
        or "Describe the defensive pattern, relevant IOCs, and recommended response."
    ).strip()
    category = str(candidate.get("category") or "security").strip() or "security"
    skill_name = _skill_name(candidate, title)
    description = f"Security guidance for {title}."

    return {
        "name": skill_name,
        "description": description,
        "category": category,
        "content": _render_skill_md(
            name=skill_name,
            description=description,
            title=title,
            problem=problem,
            scope=scope,
            evidence=evidence,
            recommended_response=recommended_response,
        ),
    }


def apply_security_skill_candidate(
    candidate: dict[str, Any],
    *,
    skills_dir: str | Path | None = None,
) -> dict[str, Any]:
    spec = security_skill_candidate_to_skill_spec(candidate)
    root = Path(skills_dir) if skills_dir is not None else get_agent_skills_dir()
    skill_dir = root / spec["name"]
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        return {
            "applied": False,
            "target": "skills",
            "skill_name": spec["name"],
            "skill_path": str(skill_dir),
            "reason": "skill_exists",
        }
    if skill_dir.exists():
        raise SecuritySkillApplicationError(f"skill directory already exists: {skill_dir}")

    skill_dir.mkdir(parents=True, exist_ok=False)
    skill_file.write_text(spec["content"], encoding="utf-8")
    return {
        "applied": True,
        "target": "skills",
        "skill_name": spec["name"],
        "skill_path": str(skill_dir),
    }


def security_evolution_candidate_to_skill_patch(candidate: dict[str, Any]) -> dict[str, str]:
    if candidate.get("type") != "security_evolution":
        raise SecuritySkillApplicationError("candidate type must be security_evolution")
    if candidate.get("requires_approval") is not True:
        raise SecuritySkillApplicationError("candidate requires approval")

    skill_name = str(candidate.get("skill_name") or "").strip()
    if not skill_name:
        raise SecuritySkillApplicationError("skill_name must be non-empty")

    section = str(candidate.get("section") or "Troubleshooting").strip()
    if not section:
        raise SecuritySkillApplicationError("section must be non-empty")

    content = str(candidate.get("content") or "").strip()
    if not content:
        raise SecuritySkillApplicationError("content must be non-empty")

    evidence = _coerce_non_empty_list(candidate.get("evidence"), "evidence")
    return {
        "skill_name": skill_name,
        "section": section,
        "content": _render_evolution_block(content=content, evidence=evidence),
    }


def apply_security_evolution_candidate(
    candidate: dict[str, Any],
    *,
    skills_dir: str | Path | None = None,
) -> dict[str, Any]:
    patch = security_evolution_candidate_to_skill_patch(candidate)
    root = Path(skills_dir) if skills_dir is not None else get_agent_skills_dir()
    skill_dir = _resolve_skill_dir(root, patch["skill_name"])
    if skill_dir is None:
        raise SecuritySkillApplicationError(f"skill not found: {patch['skill_name']}")

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise SecuritySkillApplicationError(f"SKILL.md not found for skill: {patch['skill_name']}")

    original = skill_file.read_text(encoding="utf-8", errors="replace")
    if patch["content"] in original:
        return {
            "applied": False,
            "target": "skills",
            "skill_name": patch["skill_name"],
            "skill_path": str(skill_dir),
            "reason": "already_applied",
        }

    updated = _append_to_markdown_section(
        original,
        section=patch["section"],
        content=patch["content"],
    )
    skill_file.write_text(updated, encoding="utf-8")
    return {
        "applied": True,
        "target": "skills",
        "skill_name": patch["skill_name"],
        "skill_path": str(skill_dir),
        "section": patch["section"],
    }


def _render_skill_md(
    *,
    name: str,
    description: str,
    title: str,
    problem: str,
    scope: str,
    evidence: list[str],
    recommended_response: str,
) -> str:
    evidence_lines = "\n".join(f"- {_single_line(item)}" for item in evidence)
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Security Pattern\n\n"
        f"{problem}\n\n"
        "## Evidence\n\n"
        f"{evidence_lines}\n\n"
        "## Recommended Response\n\n"
        f"{recommended_response}\n\n"
        "## Operational Guidance\n\n"
        f"{scope}\n"
    )


def _render_evolution_block(*, content: str, evidence: list[str]) -> str:
    evidence_lines = "\n".join(f"- {_single_line(item)}" for item in evidence)
    return (
        "### Security Review Update\n\n"
        f"{content}\n\n"
        "Evidence:\n"
        f"{evidence_lines}\n"
    )


def _skill_name(candidate: dict[str, Any], title: str) -> str:
    raw = str(candidate.get("skill_name") or candidate.get("name") or title).strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw.lower()).strip("-") or "security-review"
    if not slug.startswith("security-"):
        slug = f"security-{slug}"
    return slug[:80].strip("-") or "security-review"


def _resolve_skill_dir(root: Path, skill_name: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", skill_name):
        raise SecuritySkillApplicationError("skill_name contains invalid characters")

    direct = root / skill_name
    if direct.is_dir() and (direct / "SKILL.md").is_file():
        return direct
    if not root.is_dir():
        return None

    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        if _frontmatter_name(text) == skill_name:
            return child
    return None


def _frontmatter_name(text: str) -> str:
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def _append_to_markdown_section(text: str, *, section: str, content: str) -> str:
    lines = text.splitlines()
    heading_pattern = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
    target_index: int | None = None
    target_level = 2
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if not match:
            continue
        if match.group(2).strip().lower() == section.strip().lower():
            target_index = index
            target_level = len(match.group(1))
            break

    block_lines = ["", content.strip()]
    if target_index is None:
        base = text.rstrip()
        return f"{base}\n\n## {section}\n{content.strip()}\n"

    insert_at = len(lines)
    for index in range(target_index + 1, len(lines)):
        match = heading_pattern.match(lines[index])
        if match and len(match.group(1)) <= target_level:
            insert_at = index
            break
    updated_lines = lines[:insert_at] + block_lines + lines[insert_at:]
    return "\n".join(updated_lines).rstrip() + "\n"


def _coerce_non_empty_list(value: Any, field_name: str) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    normalized = [str(item).strip() for item in items if str(item).strip()]
    if not normalized:
        raise SecuritySkillApplicationError(f"{field_name} must be non-empty")
    return normalized


def _single_line(value: str) -> str:
    return " ".join(str(value).split())
