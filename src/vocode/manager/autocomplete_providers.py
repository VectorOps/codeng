from __future__ import annotations
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from vocode.logger import logger
from .autocomplete import AutocompleteManager, AutocompleteItem
from vocode import settings as vocode_settings
from .commands import auth as auth_commands
from .commands import mcp as mcp_commands

if TYPE_CHECKING:
    from .server import UIServer


MIN_FILE_AUTOCOMPLETE_LEN = 2
MAX_VAR_AUTOCOMPLETE_ITEMS = 10
FILESYSTEM_AUTOCOMPLETE_LIMIT = 5
FILESYSTEM_AUTOCOMPLETE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    "__pycache__",
    "node_modules",
    "venv",
}


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


def _score_filesystem_candidate(candidate: str, needle: str) -> Optional[int]:
    candidate_key = candidate.casefold()
    needle_key = needle.casefold()
    if candidate_key.startswith(needle_key):
        return 0

    candidate_name = candidate.rstrip("/").rsplit("/", 1)[-1].casefold()
    needle_name = needle_key.rsplit("/", 1)[-1]
    if needle_name and candidate_name.startswith(needle_name):
        return 1
    if needle_key in candidate_key:
        return 2
    if needle_name and needle_name in candidate_name:
        return 3
    return None


def _filesystem_autocomplete_items(
    paths: list[str],
    needle: str,
    start: int,
    word: str,
) -> list[AutocompleteItem]:
    normalized_needle = needle.replace("\\", "/").lstrip("./")
    if not normalized_needle:
        return []

    matches: list[tuple[int, str]] = []
    for rel_path in paths:
        score = _score_filesystem_candidate(rel_path, normalized_needle)
        if score is not None:
            matches.append((score, rel_path))

    matches.sort(key=lambda item: (item[0], item[1]))
    deduped_paths: list[str] = []
    seen_paths: set[str] = set()
    for _, rel_path in matches:
        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        deduped_paths.append(rel_path)
        if len(deduped_paths) >= FILESYSTEM_AUTOCOMPLETE_LIMIT:
            break

    return [
        AutocompleteItem(
            title=rel_path,
            replace_start=start,
            replace_text=word,
            insert_text=rel_path,
        )
        for rel_path in deduped_paths
    ]


async def _know_autocomplete_items(
    server: "UIServer",
    needle: str,
    start: int,
    word: str,
) -> list[AutocompleteItem] | None:
    project = server.manager.project
    try:
        kp = project.know
    except AttributeError:
        return None
    if kp is None:
        return None

    try:
        repo_ids: Optional[list[str]] = kp.pm.repo_ids
        files = await kp.data.file.filename_complete(
            needle=needle,
            repo_ids=repo_ids,
            limit=FILESYSTEM_AUTOCOMPLETE_LIMIT,
        )
    except Exception as exc:
        logger.debug(
            "file autocomplete falling back to filesystem",
            error=str(exc),
        )
        return None

    return [
        AutocompleteItem(
            title=f.path,
            replace_start=start,
            replace_text=word,
            insert_text=f.path,
        )
        for f in files
        if f.path
    ]


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
        items = await _know_autocomplete_items(server, needle, start, word)
        if items is None:
            paths = await server.file_path_cache.get_paths()
            items = _filesystem_autocomplete_items(
                paths,
                needle,
                start,
                word,
            )
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
async def auth_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    _ = server
    if not text:
        return None
    if row != 0:
        return None
    if not text.startswith("/auth"):
        return None
    if text != "/auth" and not text.startswith("/auth "):
        return None

    cursor = _clamp_cursor(text, col)
    start, end = _token_span(text, cursor)
    word = text[start:end]
    tokens = text.split()
    has_trailing_space = text.endswith(" ")

    if len(tokens) <= 1:
        items = [
            AutocompleteItem(
                title=f"/auth {name}",
                replace_start=0,
                replace_text=text,
                insert_text=f"/auth {name} ",
            )
            for name in auth_commands.AUTH_SUBCOMMANDS
        ]
        return _filter_noop(text, items) or None

    action = tokens[1]
    if len(tokens) == 2 and not has_trailing_space:
        needle = word
        items = []
        for name in auth_commands.AUTH_SUBCOMMANDS:
            if needle and not name.startswith(needle):
                continue
            items.append(
                AutocompleteItem(
                    title=f"/auth {name}",
                    replace_start=start,
                    replace_text=word,
                    insert_text=f"{name} ",
                )
            )
        return _filter_noop(text, items) or None

    if action == "cancel":
        return None

    if action not in auth_commands.AUTH_SUBCOMMANDS:
        return None

    if len(tokens) == 2 or (len(tokens) == 3 and not has_trailing_space):
        needle = ""
        replace_text = word
        if len(tokens) == 3:
            needle = tokens[2]
        items = []
        for provider in auth_commands.AUTH_PROVIDERS:
            if needle and not provider.startswith(needle):
                continue
            items.append(
                AutocompleteItem(
                    title=f"/auth {action} {provider}",
                    replace_start=start,
                    replace_text=replace_text,
                    insert_text=provider,
                )
            )
        return _filter_noop(text, items) or None

    return None


@AutocompleteManager.register_default
async def mcp_autocomplete_provider(
    server: "UIServer",
    text: str,
    row: int,
    col: int,
) -> list[AutocompleteItem] | None:
    if not text:
        return None
    if row != 0:
        return None
    if not text.startswith("/mcp"):
        return None
    if text != "/mcp" and not text.startswith("/mcp "):
        return None

    cursor = _clamp_cursor(text, col)
    start, end = _token_span(text, cursor)
    word = text[start:end]
    tokens = text.split()
    has_trailing_space = text.endswith(" ")

    if len(tokens) <= 1:
        items = [
            AutocompleteItem(
                title=f"/mcp {name}",
                replace_start=0,
                replace_text=text,
                insert_text=f"/mcp {name} ",
            )
            for name in mcp_commands.MCP_SUBCOMMANDS
        ]
        return _filter_noop(text, items) or None

    action = tokens[1]
    if len(tokens) == 2 and not has_trailing_space:
        needle = word
        items = []
        for name in mcp_commands.MCP_SUBCOMMANDS:
            if needle and not name.startswith(needle):
                continue
            items.append(
                AutocompleteItem(
                    title=f"/mcp {name}",
                    replace_start=start,
                    replace_text=word,
                    insert_text=f"{name} ",
                )
            )
        return _filter_noop(text, items) or None

    if action == "cancel":
        return None

    if action not in mcp_commands.MCP_SUBCOMMANDS:
        return None

    if action in {"list", "status"} and len(tokens) >= 3 and has_trailing_space:
        return None

    settings = server.manager.project.settings
    if settings is None or settings.mcp is None:
        return None

    source_names: list[str] = []
    for source_name, source_settings in settings.mcp.sources.items():
        if action in {"list", "status"}:
            source_names.append(source_name)
            continue
        if not isinstance(
            source_settings,
            vocode_settings.MCPExternalSourceSettings,
        ):
            continue
        if source_settings.auth is None or not source_settings.auth.enabled:
            continue
        source_names.append(source_name)
    source_names.sort()
    if len(tokens) == 2 or (len(tokens) == 3 and not has_trailing_space):
        needle = ""
        replace_text = word
        if len(tokens) == 3:
            needle = tokens[2]
        items = []
        for source_name in source_names:
            if needle and not source_name.startswith(needle):
                continue
            items.append(
                AutocompleteItem(
                    title=f"/mcp {action} {source_name}",
                    replace_start=start,
                    replace_text=replace_text,
                    insert_text=source_name,
                )
            )
        return _filter_noop(text, items) or None

    return None


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
