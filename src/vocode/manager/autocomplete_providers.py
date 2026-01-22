from __future__ import annotations
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from vocode.logger import logger
from .autocomplete import AutocompleteManager, AutocompleteItem

if TYPE_CHECKING:
    from .server import UIServer


MIN_FILE_AUTOCOMPLETE_LEN = 2


def _clamp_cursor(text: str, cursor: int) -> int:
    if cursor < 0:
        return 0
    if cursor > len(text):
        return len(text)
    return cursor


def _token_span(text: str, cursor: int) -> tuple[int, int]:
    cursor = _clamp_cursor(text, cursor)
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def _filter_noop(text: str, items: list[AutocompleteItem]) -> list[AutocompleteItem]:
    return list(items)


@AutocompleteManager.register_default
async def file_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    _ = row
    if not text:
        return None

    cursor = _clamp_cursor(text, col)
    start, end = _token_span(text, cursor)
    word = text[start:end]
    if not word or not word.startswith("@"):
        return None

    needle = word[1:].strip()
    if len(needle) < MIN_FILE_AUTOCOMPLETE_LEN:
        return None

    try:
        project = server.manager.project
        kp = project.know
        repo_ids: Optional[list[str]] = kp.pm.repo_ids
        limit = 5
        files = await kp.data.file.filename_complete(
            needle=needle,
            repo_ids=repo_ids,
            limit=limit,
        )
        items = [
            AutocompleteItem(
                title=f.path,
                replace_start=start,
                replace_text=word,
                insert_text=f.path,
            )
            for f in files
            if f.path
        ]
        return _filter_noop(text, items) or None
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("file_autocomplete_provider error", exc=exc)
        return None


@AutocompleteManager.register_default
async def command_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    if not text:
        return None

    if row != 0:
        return None

    if not text.startswith("/"):
        return None

    cursor = _clamp_cursor(text, col)
    start, end = _token_span(text, cursor)
    word = text[start:end]
    if not word or not word.startswith("/"):
        return None

    needle = word[1:]

    manager = server.commands
    help_entries = manager.get_help_entries()

    items: list[AutocompleteItem] = []
    for name, description, params in help_entries:
        if not name.startswith(needle):
            continue
        signature = "/" + name
        title = signature
        if description:
            title = f"{signature} - {description}"
        insert_text = signature
        if not insert_text.endswith(" "):
            insert_text += " "
        items.append(
            AutocompleteItem(
                title=title,
                replace_start=start,
                replace_text=word,
                insert_text=insert_text,
            )
        )

    return _filter_noop(text, items) or None


@AutocompleteManager.register_default
async def run_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    _ = col
    if not text:
        return None

    if row != 0:
        return None

    prefix = "/run"
    if not text.startswith(prefix):
        return None

    if text != prefix and not text.startswith(prefix + " "):
        return None

    needle = ""
    if text.startswith(prefix + " "):
        needle = text[len(prefix) + 1 :]

    project = server.manager.project
    settings = project.settings
    if settings is None:
        return None

    workflows = sorted(settings.workflows.keys())
    if not workflows:
        return None

    items: list[AutocompleteItem] = []
    for wf in workflows:
        if needle and not wf.startswith(needle):
            continue
        title = f"/run {wf} - workflow"
        items.append(
            AutocompleteItem(
                title=title,
                replace_start=0,
                replace_text=text,
                insert_text=f"/run {wf}",
            )
        )

    return _filter_noop(text, items) or None
