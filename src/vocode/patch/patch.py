from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple
import os
import re
from .models import FileApplyStatus


DIFF_PATCH_SYSTEM_INSTRUCTION = r"""# Patch format: SEARCH/REPLACE blocks

**IMPORTANT:** You implement exactly the ARCHITECT PLAN. Follow repo style. Keep edits minimal. No speculation. No reformatting. No new deps unless the plan says so.

**OUTPUT:** Only patch blocks. No prose before/between/after.

## Format
Emit exactly one SEARCH/REPLACE fenced block per change using the file’s language tag:

```<lang>
<full/path/to/file>
<<<<<<< SEARCH
<contiguous lines that EXACTLY match current content>
=======
<replacement lines>
>>>>>>> REPLACE
```

Edits: use the format above.
Adds (new file): leave SEARCH empty; put full file contents in REPLACE.
Deletes: put entire current file in SEARCH; leave REPLACE empty.

## Rules
1. SEARCH must match character-for-character (whitespace, quotes, comments, docstrings).
2. Include enough lines in SEARCH to uniquely identify lines being replaced.
3. No other diff headers, line numbers, or markers.
4. Keep changes narrowly scoped; avoid touching unrelated code.
5. Use existing libraries/patterns; keep imports/types/names consistent.
6. SEARCH/REPLACE will only change first occurence.
7. Keep all changes small and compact. Break larger changes into series of SEARCH/REPLACE blocks.
8. You are allowed to emit multiple blocks per file, but blocks should not overlap. Each block must have it's own fence.
9. Avoid emitting complete files if they have multiple changes. Emit multiple blocks per file instead.

## Self-check before emitting
1. All planned changes covered?
2. SEARCH sections exact? Imports/types/tests correct? Unrelated edits avoided?
3. Are changes as small as possible?
"""


class ActionType(Enum):
    ADD = auto()
    UPDATE = auto()
    DELETE = auto()


@dataclass
class PatchAction:
    type: ActionType
    search: List[str] = field(default_factory=list)
    replace: List[str] = field(default_factory=list)
    start_line: Optional[int] = None


@dataclass
class Patch:
    # Map file path -> ordered list of actions (blocks) for that file
    actions: Dict[str, List[PatchAction]] = field(default_factory=dict)


@dataclass
class PatchError:
    msg: str
    line: Optional[int] = None
    hint: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class FileChange:
    type: ActionType
    old_content: Optional[str] = None
    new_content: Optional[str] = None


@dataclass
class Commit:
    changes: Dict[str, FileChange] = field(default_factory=dict)


FENCE_RE = re.compile(r"^```")
SEARCH_MARK = "<<<<<<< SEARCH"
SPLIT_MARK = "======="
REPLACE_MARK = ">>>>>>> REPLACE"


def _is_relative_path(p: str) -> bool:
    if not p:
        return False
    if p.startswith("/") or p.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", p):
        return False
    norm = os.path.normpath(p)
    return not os.path.isabs(norm)


def parse_patch(text: str) -> Tuple[Patch, List[PatchError]]:
    """
    Parse fenced SEARCH/REPLACE blocks:

    ```<lang>
    <full/path/to/file>
    <<<<<<< SEARCH
    <contiguous current content>
    =======
    <replacement content>
    >>>>>>> REPLACE
    ```
    Adds: SEARCH empty, REPLACE has full content.
    Deletes: SEARCH has full content, REPLACE empty.
    Updates: both non-empty.
    """
    errors: List[PatchError] = []
    patch = Patch()
    lines = text.splitlines()
    i = 0
    # Multiple blocks per file are allowed; maintain order of appearance
    # actions will be appended to patch.actions for each block encountered

    def add_error(
        msg: str,
        *,
        line: Optional[int] = None,
        hint: Optional[str] = None,
        filename: Optional[str] = None,
    ):
        errors.append(PatchError(msg=msg, line=line, hint=hint, filename=filename))

    while i < len(lines):
        line = lines[i]
        if not FENCE_RE.match(line.strip()):
            i += 1
            continue

        fence_start_line = i + 1
        i += 1
        if i >= len(lines):
            add_error(
                "Unterminated code fence",
                line=fence_start_line,
                hint="Add closing `` for the patch block",
            )
            break

        # First line inside fence must be the file path
        path_line_no = i + 1
        path = lines[i].strip()
        i += 1
        if not _is_relative_path(path):
            add_error(
                f"Path must be relative: {path!r}",
                line=path_line_no,
                hint="Use a relative repo path",
                filename=path,
            )
            # Skip to next fence end
            while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
                i += 1
            if i < len(lines):
                i += 1
            continue
        # Multiple blocks per file are allowed; we append each parsed block to patch.actions[path]

        # Expect SEARCH marker
        if i >= len(lines) or lines[i].strip() != SEARCH_MARK:
            add_error("Missing <<<<<<< SEARCH marker", line=i + 1, filename=path)
            # Skip to next fence end
            while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
                i += 1
            if i < len(lines):
                i += 1
            continue
        i += 1

        # Collect SEARCH lines until SPLIT_MARK
        search_lines: List[str] = []
        while i < len(lines) and lines[i].strip() != SPLIT_MARK:
            # Stop if fence closed unexpectedly
            if FENCE_RE.match(lines[i].strip()):
                add_error("Missing ======= split marker", line=i + 1, filename=path)
                break
            search_lines.append(lines[i])
            i += 1

        if i >= len(lines) or lines[i].strip() != SPLIT_MARK:
            # Try to fast-forward to fence end
            while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
                i += 1
            if i < len(lines):
                i += 1
            continue
        i += 1  # skip SPLIT_MARK

        # Collect REPLACE lines until REPLACE_MARK
        replace_lines: List[str] = []
        while i < len(lines) and lines[i].strip() != REPLACE_MARK:
            if FENCE_RE.match(lines[i].strip()):
                add_error("Missing >>>>>>> REPLACE marker", line=i + 1, filename=path)
                break
            replace_lines.append(lines[i])
            i += 1

        if i >= len(lines) or lines[i].strip() != REPLACE_MARK:
            while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
                i += 1
            if i < len(lines):
                i += 1
            continue
        i += 1  # skip REPLACE_MARK

        # Expect closing fence
        if i >= len(lines) or not FENCE_RE.match(lines[i].strip()):
            add_error(
                "Missing closing code fence ``",
                line=i + 1 if i < len(lines) else None,
                filename=path,
            )
            # Attempt to continue scanning
            while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
                i += 1
            if i < len(lines):
                i += 1
        else:
            i += 1  # consume closing fence

        # Normalize: treat a single empty line as empty content
        if len(search_lines) == 1 and search_lines[0] == "":
            search_lines = []
        if len(replace_lines) == 1 and replace_lines[0] == "":
            replace_lines = []

        # Determine action type (after normalization above)
        if search_lines and not replace_lines:
            action_type = ActionType.DELETE
        elif replace_lines and not search_lines:
            action_type = ActionType.ADD
        elif search_lines and replace_lines:
            action_type = ActionType.UPDATE
        else:
            add_error(
                "Empty patch block (no SEARCH and no REPLACE content)",
                line=path_line_no,
                filename=path,
            )
            continue
        # Append block to this file's action list
        patch.actions.setdefault(path, []).append(
            PatchAction(
                type=action_type,
                search=search_lines,
                replace=replace_lines,
                start_line=path_line_no,
            )
        )

    return patch, errors


def load_files(
    paths: List[str], open_fn: Callable[[str], str]
) -> Tuple[Dict[str, str], List[PatchError]]:
    files: Dict[str, str] = {}
    errs: List[PatchError] = []
    for path in paths:
        try:
            files[path] = open_fn(path)
        except Exception as e:
            errs.append(
                PatchError(
                    msg=f"Failed to read file: {path}",
                    hint=f"{type(e).__name__}: {e}",
                    filename=path,
                )
            )
    return files, errs


def _join_lines(lines: List[str], *, eol: bool) -> str:
    s = "\n".join(lines)
    return s + ("\n" if eol else "")


def _find_subsequence(hay: List[str], needle: List[str]) -> Optional[int]:
    if not needle:
        return None
    n, m = len(hay), len(needle)
    if m > n:
        return None
    for start in range(0, n - m + 1):
        if hay[start : start + m] == needle:
            return start
    return None


def build_commits(
    patch: Patch, files: Dict[str, str]
) -> Tuple[List[Commit], List[PatchError], Dict[str, FileApplyStatus]]:
    commits: List[Commit] = []
    errors: List[PatchError] = []
    changes: Dict[str, FileChange] = {}
    status_map: Dict[str, FileApplyStatus] = {}

    def add_error(
        msg: str,
        *,
        line: Optional[int] = None,
        hint: Optional[str] = None,
        filename: Optional[str] = None,
    ):
        errors.append(PatchError(msg=msg, line=line, hint=hint, filename=filename))

    for path, actions in patch.actions.items():
        if not actions:
            continue

        # Determine initial content source
        first = actions[0]
        deleted = False
        any_failed = False
        applied_any = False  # at least one UPDATE applied successfully

        if first.type == ActionType.ADD:
            # Start from provided content (no trailing newline by default for patch mode)
            file_lines = first.replace[:]
            had_eol = False
            action_iter = actions[1:]
            existed = False
        elif first.type == ActionType.DELETE:
            # Delete-only sequence: no need to read existing file
            deleted = True
            action_iter = []
            existed = True  # treat as existing target namespace; we won't read content
        else:
            # UPDATE sequence must have loaded content
            original = files.get(path)
            if original is None:
                add_error(
                    f"No loaded content for file: {path}",
                    hint="Ensure the file exists and is readable for update.",
                    filename=path,
                    line=first.start_line,
                )
                status_map[path] = FileApplyStatus.PartialUpdate
                continue
            had_eol = original.endswith("\n")
            file_lines = original.splitlines()
            action_iter = actions
            existed = True
            original_content = original

        # Apply actions sequentially (if not already marked for delete)
        for act in action_iter:
            if act.type == ActionType.ADD:
                add_error(
                    f"Ignoring Add block not at start for {path}",
                    line=act.start_line,
                    hint="Only the first block may be an Add for a new file",
                    filename=path,
                )
                any_failed = True
                continue

            if act.type == ActionType.DELETE:
                deleted = True
                break

            # UPDATE
            start_idx = _find_subsequence(file_lines, act.search)
            if start_idx is None:
                block_text = "\n".join(act.search)
                add_error(
                    f"Failed to locate exact SEARCH block in {path}",
                    hint=f"SEARCH content must match current file exactly. Block not found:\n---\n{block_text}\n---",
                    filename=path,
                    line=act.start_line,
                )
                any_failed = True
                continue
            end_idx = start_idx + len(act.search)
            file_lines = file_lines[:start_idx] + act.replace + file_lines[end_idx:]
            applied_any = True

        # Finalize change for this path
        if deleted:
            changes[path] = FileChange(type=ActionType.DELETE)
            status_map[path] = FileApplyStatus.Delete
            continue

        if not existed:
            new_content = _join_lines(
                file_lines, eol=had_eol
            )  # had_eol set above for add branch as False
            changes[path] = FileChange(type=ActionType.ADD, new_content=new_content)
            status_map[path] = (
                FileApplyStatus.PartialUpdate if any_failed else FileApplyStatus.Create
            )
        else:
            # Only emit an UPDATE change if at least one update actually applied
            status_map[path] = (
                FileApplyStatus.PartialUpdate if any_failed else FileApplyStatus.Update
            )
            if applied_any:
                new_content = _join_lines(file_lines, eol=had_eol)
                changes[path] = FileChange(
                    type=ActionType.UPDATE,
                    old_content=original_content,
                    new_content=new_content,
                )

    if changes:
        commits.append(Commit(changes=changes))

    return commits, errors, status_map


def apply_commits(
    commits: List[Commit],
    write_fn: Callable[[str, str], None],
    delete_fn: Callable[[str], None],
) -> List[PatchError]:
    errors: List[PatchError] = []
    for commit in commits:
        for path, change in commit.changes.items():
            try:
                if change.type == ActionType.ADD or change.type == ActionType.UPDATE:
                    write_fn(path, change.new_content or "")
                elif change.type == ActionType.DELETE:
                    delete_fn(path)
                else:
                    errors.append(
                        PatchError(
                            msg=f"Unknown change type for {path}",
                            hint="Supported types are Add/Update/Delete",
                            filename=path,
                        )
                    )
            except Exception as e:
                errors.append(
                    PatchError(
                        msg=f"Failed to apply change to file: {path}",
                        hint=f"{type(e).__name__}: {e}",
                        filename=path,
                    )
                )
    return errors


def process_patch(
    text: str,
    open_fn: Callable[[str], str],
    write_fn: Callable[[str, str], None],
    delete_fn: Callable[[str], None],
) -> Tuple[Dict[str, FileApplyStatus], List[PatchError]]:
    # 1) Parse
    patch, parse_errors = parse_patch(text)
    if parse_errors:
        return {}, parse_errors
    # 2) Load files needed:
    #    For paths with UPDATE blocks where the first block is not an ADD.
    to_read: List[str] = []
    for p, acts in patch.actions.items():
        if not acts:
            continue
        first = acts[0]
        needs_read = (
            any(a.type == ActionType.UPDATE for a in acts)
            and first.type != ActionType.ADD
        )
        if needs_read:
            to_read.append(p)
    files, read_errors = load_files(to_read, open_fn)
    if read_errors:
        return {}, read_errors

    # 3) Build commits
    commits, build_errors, status_map = build_commits(patch, files)

    # 4) Apply commits (partial application OK)
    apply_errors = apply_commits(commits, write_fn, delete_fn)

    return status_map, [*build_errors, *apply_errors]
