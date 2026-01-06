from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

from vocode.lib.markdown import parse_yaml_frontmatter


logger = logging.getLogger(__name__)


class Skill(BaseModel):
    """Represents a single project skill loaded from a SKILL.md file.

    Only a subset of frontmatter fields is modeled explicitly; the full
    frontmatter is preserved in ``frontmatter`` for debugging or future use.
    """

    name: str
    description: str
    path: Path
    frontmatter: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        # Lowercase letters, digits, underscores and hyphens only; max length 64.
        if not value:
            raise ValueError("Skill name must be non-empty")
        if len(value) > 64:
            raise ValueError("Skill name must be at most 64 characters long")
        for ch in value:
            if ch.islower() or ch.isdigit() or ch == "-" or ch == "_":
                continue
            raise ValueError(
                "Skill name may only contain lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        if not value:
            raise ValueError("Skill description must be non-empty")
        if len(value) > 1024:
            raise ValueError("Skill description must be at most 1024 characters long")
        return value


def _coerce_allowed_tools(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        # Support comma-separated list in a single string.
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        out: List[str] = []
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
            else:
                value = str(item).strip()
            if value:
                out.append(value)
        return out
    return []


def discover_skills(base_path: Path) -> List[Skill]:
    """Discover skills under ``.vocode/skills``.

    A skill is a directory directly under ``.vocode/skills`` containing a
    ``SKILL.md`` file with a valid YAML frontmatter block. Invalid or
    malformed skill files are ignored.
    """

    skills_dir = base_path / ".vocode" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: List[Skill] = []
    for child in skills_dir.iterdir():
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        frontmatter = parse_yaml_frontmatter(skill_file)
        if not isinstance(frontmatter, dict):
            logger.warning(
                "Ignoring skill at %s: missing or invalid YAML frontmatter", skill_file
            )
            continue

        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            logger.warning(
                "Ignoring skill at %s: 'name' or 'description' missing or not a string",
                skill_file,
            )
            continue

        allowed_tools = _coerce_allowed_tools(frontmatter.get("allowed-tools"))

        try:
            skill = Skill(
                name=name,
                description=description,
                path=skill_file,
                allowed_tools=allowed_tools,
                frontmatter=frontmatter,
            )
        except Exception as exc:
            # Skip invalid skills; callers only see successfully parsed entries.
            logger.warning(
                "Ignoring skill at %s due to validation error: %s",
                skill_file,
                exc,
            )
            continue

        skills.append(skill)

    return skills
