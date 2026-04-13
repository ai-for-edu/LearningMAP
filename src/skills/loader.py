"""Skill loader: scan skills/ directory, parse SKILL.md files.

Each skill is a directory under skills/ containing a SKILL.md file with:
- YAML frontmatter (name, display_name, description)
- Markdown body split by ## section_name headers

Sections map to LangGraph node names (chat, quiz, lesson, knowledge_graph).
The `affects` list is auto-inferred from which sections exist.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


@dataclass(frozen=True)
class SkillMeta:
    """Parsed representation of a SKILL.md file."""

    name: str
    display_name: str
    description: str
    affects: tuple[str, ...]
    sections: MappingProxyType  # str → str, truly immutable
    raw_body: str


# Module-level registry, populated by load_skills()
_registry: dict[str, SkillMeta] = {}


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from markdown body.

    Returns (frontmatter_dict, body_str). If no frontmatter found,
    returns ({}, full_text).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n(.*))?$", text, re.DOTALL)
    if not match:
        return {}, text

    yaml_str = match.group(1)
    body = match.group(2) or ""

    # Minimal single-pass YAML parser (avoids PyYAML dependency)
    fm: dict[str, Any] = {}
    current_list_key: str | None = None
    current_list: list[str] = []

    for line in yaml_str.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_list_key:
                current_list.append(stripped[2:].strip())
            continue
        if ":" in stripped:
            # Flush previous list
            if current_list_key and current_list:
                fm[current_list_key] = current_list
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if not value:
                current_list_key = key
                current_list = []
            else:
                current_list_key = None
                current_list = []
                fm[key] = value

    # Flush last list
    if current_list_key and current_list:
        fm[current_list_key] = current_list

    return fm, body


def _parse_sections(body: str) -> dict[str, str]:
    """Split markdown body by ## headers into named sections.

    Returns dict mapping section_name → section_content.
    Content before the first ## header is ignored.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        header_match = re.match(r"^##\s+(.+?)\s*$", line)
        if header_match:
            # Save previous section
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip()
            # Normalize: "Knowledge Graph" → "knowledge_graph"
            current_name = header_match.group(1).strip().lower().replace(" ", "_")
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    # Save last section
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


def _load_one(skill_dir: Path) -> SkillMeta | None:
    """Load a single SKILL.md from a directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    try:
        text = skill_file.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read %s", skill_file, exc_info=True)
        return None

    fm, body = _parse_frontmatter(text)
    sections = _parse_sections(body)

    name = fm.get("name", skill_dir.name)
    display_name = fm.get("display_name", name)
    description = fm.get("description", "")

    # Auto-infer affects from actual sections
    affects = tuple(sections.keys())

    if not sections:
        logger.warning("Skill %s has no ## sections, it will have no effect", name)

    return SkillMeta(
        name=name,
        display_name=display_name,
        description=description,
        affects=affects,
        sections=MappingProxyType(sections),
        raw_body=body.strip(),
    )


def load_skills(skills_dir: Path | None = None) -> dict[str, SkillMeta]:
    """Scan skills directory and load all SKILL.md files.

    Call once at startup. Results are cached in module-level registry.
    """
    global _registry
    base = skills_dir or _SKILLS_DIR

    if not base.exists():
        logger.info("Skills directory %s does not exist, skipping", base)
        return {}

    loaded: dict[str, SkillMeta] = {}
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        skill = _load_one(child)
        if skill:
            loaded[skill.name] = skill
            logger.info(
                "Loaded skill: %s (%s) affects=%s",
                skill.name, skill.display_name, skill.affects,
            )

    _registry = loaded
    logger.info("Total skills loaded: %d", len(loaded))
    return loaded


def get_skill(name: str) -> SkillMeta | None:
    """Get a loaded skill by name."""
    return _registry.get(name)


def list_skills() -> list[dict[str, Any]]:
    """Return skill summaries for API / intent prompt."""
    return [
        {
            "name": s.name,
            "display_name": s.display_name,
            "description": s.description,
            "sections": list(s.affects),
            "body": s.raw_body,
        }
        for s in _registry.values()
    ]


def get_skill_section(state: dict[str, Any], node_name: str) -> str:
    """Extract the skill instruction for a specific node from state.

    Returns the node-specific section content, or empty string if
    no skill is active or the skill has no section for this node.
    """
    content = state.get("active_skill_content")
    if not content:
        return ""
    return content.get(node_name, "")
