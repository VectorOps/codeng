from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def parse_yaml_frontmatter(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a leading YAML frontmatter block from a Markdown file.

    Expects a starting line ``---`` followed by YAML and a closing ``---``
    delimiter. Returns the parsed mapping, or None when no valid frontmatter
    block is present or parsing fails.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    text = text.lstrip("\ufeff")  # strip optional BOM
    if not text.startswith("---"):
        return None

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    header_lines: List[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        header_lines.append(line)
    else:
        # No closing delimiter found
        return None

    header_text = "\n".join(header_lines)
    if not header_text.strip():
        return None

    try:
        data = yaml.safe_load(header_text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    return data
