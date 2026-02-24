from __future__ import annotations
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from vocode.logger import logger
from .autocomplete import AutocompleteManager, AutocompleteItem
from vocode import settings as vocode_settings

if TYPE_CHECKING:
    from .server import UIServer


MIN_FILE_AUTOCOMPLETE_LEN = 2
MAX_VAR_AUTOCOMPLETE_ITEMS = 10


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
async def var_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    if not text:
        return None
    if row != 0:
        return None
    if not text.startswith("/var"):
        return None

    if text != "/var" and not text.startswith("/var "):
        return None

    cursor = _clamp_cursor(text, col)
    start, end = _token_span(text, cursor)
    word = text[start:end]

    tokens = text.split()
    has_trailing_space = text.endswith(" ")

    def make_items(values: list[str], *, replace_text: str) -> list[AutocompleteItem]:
        out: list[AutocompleteItem] = []
        for val in values:
            out.append(
                AutocompleteItem(
                    title=val,
                    replace_start=start,
                    replace_text=replace_text,
                    insert_text=val,
                )
            )
        return out

    if len(tokens) <= 1:
        needle = ""
        if word.startswith("/var"):
            needle = word[len("/var") :]
        items = []
        for sub in ("list", "set"):
            if needle and not sub.startswith(needle):
                continue
            items.append(
                AutocompleteItem(
                    title=f"/var {sub}",
                    replace_start=0,
                    replace_text=text,
                    insert_text=f"/var {sub} ",
                )
            )
        return _filter_noop(text, items) or None

    sub = tokens[1]
    if sub != "set":
        return None

    settings = server.manager.project.settings
    if settings is None or not isinstance(settings, vocode_settings.Settings):
        return None

    var_defs = settings.list_variables()
    var_names = sorted(var_defs.keys())
    if len(tokens) == 2 or (len(tokens) == 3 and not has_trailing_space):
        needle = ""
        replace = word
        if len(tokens) >= 3:
            needle = tokens[2]
        if not replace and len(tokens) == 2 and has_trailing_space:
            replace = ""
        items: list[AutocompleteItem] = []
        for name in var_names:
            if needle and not name.startswith(needle):
                continue
            insert = name
            if not insert.endswith(" "):
                insert += " "
            items.append(
                AutocompleteItem(
                    title=f"{name} - variable",
                    replace_start=start,
                    replace_text=replace,
                    insert_text=insert,
                )
            )
        return _filter_noop(text, items[:MAX_VAR_AUTOCOMPLETE_ITEMS]) or None

    if len(tokens) >= 3:
        var_name = tokens[2]
    else:
        return None

    needle = ""
    replace_text = word
    if len(tokens) >= 4 and not has_trailing_space:
        needle = tokens[3]
    elif has_trailing_space:
        replace_text = ""

    choices = settings.list_variable_value_choices(var_name, needle=needle)
    if not choices:
        return None

    def _stringify_choice_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                return str(value)
        return str(value)

    items: list[AutocompleteItem] = []
    for choice in choices[:MAX_VAR_AUTOCOMPLETE_ITEMS]:
        insert = _stringify_choice_value(choice.value)
        items.append(
            AutocompleteItem(
                title=choice.name,
                replace_start=start,
                replace_text=replace_text,
                insert_text=insert,
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
