# coding: utf-8
from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.deep_agent.security_review.skill_applicator import (
    SecuritySkillApplicationError,
    apply_security_evolution_candidate,
    apply_security_skill_candidate,
    security_evolution_candidate_to_skill_patch,
    security_skill_candidate_to_skill_spec,
)


def _candidate(**overrides):
    data = {
        "type": "security_skill",
        "title": "Post exploitation chain defense",
        "problem": "A session combined listener setup, remote execution, and credential access.",
        "evidence": ["listener setup", "credential access"],
        "suggested_skill_scope": "Describe the pattern, IOCs, and recommended response.",
        "category": "security",
        "requires_approval": True,
    }
    data.update(overrides)
    return data


def test_security_skill_candidate_maps_to_skill_spec():
    spec = security_skill_candidate_to_skill_spec(_candidate())

    assert spec["name"] == "security-post-exploitation-chain-defense"
    assert spec["description"].startswith("Security guidance for Post exploitation")
    assert "# Security Pattern" in spec["content"]
    assert "listener setup" in spec["content"]
    assert "credential access" in spec["content"]
    assert "recommended response" in spec["content"].lower()


def test_security_skill_candidate_rejects_unapproved_candidate():
    with pytest.raises(SecuritySkillApplicationError, match="requires approval"):
        security_skill_candidate_to_skill_spec(_candidate(requires_approval=False))


def test_security_skill_candidate_rejects_missing_evidence():
    with pytest.raises(SecuritySkillApplicationError, match="evidence"):
        security_skill_candidate_to_skill_spec(_candidate(evidence=[]))


def test_apply_security_skill_candidate_writes_skill_md(tmp_path):
    result = apply_security_skill_candidate(_candidate(), skills_dir=tmp_path)

    skill_file = tmp_path / "security-post-exploitation-chain-defense" / "SKILL.md"
    assert result["applied"] is True
    assert result["target"] == "skills"
    assert result["skill_name"] == "security-post-exploitation-chain-defense"
    assert result["skill_path"] == str(skill_file.parent)
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: security-post")


def test_apply_security_skill_candidate_does_not_overwrite_existing_skill(tmp_path):
    skill_dir = tmp_path / "security-post-exploitation-chain-defense"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("existing", encoding="utf-8")

    result = apply_security_skill_candidate(_candidate(), skills_dir=tmp_path)

    assert result["applied"] is False
    assert result["target"] == "skills"
    assert result["reason"] == "skill_exists"
    assert skill_file.read_text(encoding="utf-8") == "existing"


def _evolution_candidate(**overrides):
    data = {
        "type": "security_evolution",
        "skill_name": "safe-shell",
        "section": "Troubleshooting",
        "content": "Stop repeating blocked shell commands and request authorization.",
        "evidence": ["curl | sh was blocked twice"],
        "requires_approval": True,
    }
    data.update(overrides)
    return data


def test_security_evolution_candidate_maps_to_skill_patch():
    patch = security_evolution_candidate_to_skill_patch(_evolution_candidate())

    assert patch["skill_name"] == "safe-shell"
    assert patch["section"] == "Troubleshooting"
    assert "Stop repeating blocked shell commands" in patch["content"]
    assert "curl | sh was blocked twice" in patch["content"]


def test_security_evolution_candidate_rejects_unapproved_candidate():
    with pytest.raises(SecuritySkillApplicationError, match="requires approval"):
        security_evolution_candidate_to_skill_patch(
            _evolution_candidate(requires_approval=False)
        )


def test_apply_security_evolution_candidate_appends_to_existing_skill(tmp_path):
    skill_dir = tmp_path / "safe-shell"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: safe-shell\ndescription: \"Safe shell guidance.\"\n---\n\n"
        "# Safe Shell\n\n## Troubleshooting\n\nExisting guidance.\n",
        encoding="utf-8",
    )

    result = apply_security_evolution_candidate(_evolution_candidate(), skills_dir=tmp_path)

    updated = skill_file.read_text(encoding="utf-8")
    assert result["applied"] is True
    assert result["target"] == "skills"
    assert result["skill_name"] == "safe-shell"
    assert result["skill_path"] == str(skill_dir)
    assert "Existing guidance." in updated
    assert "Security Review Update" in updated
    assert "Stop repeating blocked shell commands" in updated
    assert "curl | sh was blocked twice" in updated


def test_apply_security_evolution_candidate_rejects_missing_skill(tmp_path):
    with pytest.raises(SecuritySkillApplicationError, match="skill not found"):
        apply_security_evolution_candidate(_evolution_candidate(), skills_dir=tmp_path)
