from __future__ import annotations

import typing

from rich import segment as rich_segment
from rich import style as rich_style
from rich import text as rich_text


Lines = typing.List[typing.List[rich_segment.Segment]]


def compact_rendered_lines(lines: Lines, max_lines: int) -> Lines:
    if max_lines < 0:
        max_lines = 0
    if len(lines) <= max_lines:
        return lines
    remaining = len(lines) - max_lines
    compacted: Lines = list(lines[:max_lines])
    suffix = rich_text.Text(
        f"... ({remaining} other lines)",
        style=rich_style.Style(dim=True),
    )
    compacted.append([rich_segment.Segment(str(suffix), suffix.style)])
    return compacted
