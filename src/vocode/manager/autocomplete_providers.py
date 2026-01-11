from __future__ import annotations
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from vocode.logger import logger
from .autocomplete import AutocompleteManager, AutocompleteItem

if TYPE_CHECKING:
    from .server import UIServer


MIN_FILE_AUTOCOMPLETE_LEN = 2


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

    cursor = col
    if cursor < 0:
        cursor = 0
    if cursor > len(text):
        cursor = len(text)

    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1

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
        return [AutocompleteItem(title=f.path, value=f.path) for f in files if f.path]
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
    _ = col
    if not text:
        return None

    if row != 0:
        return None

    if not text.startswith("/"):
        return None

    cursor = len(text)
    if cursor < 0:
        cursor = 0
    if cursor > len(text):
        cursor = len(text)

    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1

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
        items.append(AutocompleteItem(title=title, value=signature))

    return items or None


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

    if not text.startswith("/run"):
        return None

    parts = text.split()
    if not parts:
        return None
    if parts[0] != "/run":
        return None
    if len(parts) > 2:
        return None

    needle = ""
    if len(parts) == 2:
        needle = parts[1]

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
        items.append(AutocompleteItem(title=title, value=wf))

    return items or None
