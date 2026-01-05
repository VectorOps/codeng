from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple
import re
import os

from .models import FileApplyStatus


DIFF_SYSTEM_INSTRUCTION = r"""# Rules
* You must output *exactly one* fenced code block labeled patch for all changes of all files.
* No prose before or after.
* Do not wrap the patch in JSON/YAML/strings.
* Do not add backslash-escapes (\n, \t, \") or html-escapes (&quot; and similar) unless they *literally* present in the source file.
* *Never* double-escape.

Required envelope:
```patch
*** Begin Patch
[YOUR_PATCH]
*** End Patch
```

[YOUR_PATCH] is a concatenation of file sections.
Each file appears exactly once in [YOUR_PATCH].

Allowed section headers per file:
- `*** Add File: <relative/path>`
- `*** Update File: <relative/path>`
- `*** Delete File: <relative/path>`
For Update sections, you may optionally add a move directive before the first change:
- `*** Move to: <relative/new/path>`

For Update/Add files, changes are expressed with context blocks:

[0-3 lines of context before]
-<old line>
+<new line>
[0-3 lines of context after]

Update/Add blocks: exact context and edits
- Context must be an exact copy of the file lines with leading space.
- Do not escape any quotes, backslashes, or newlines. Produce the file content literally as it appears in the source, character for character.
- Preserve blank lines in context. Represent a blank context line as a completely empty line (no leading space).
- For non-blank context lines, start with a single space, then the exact text.
- Include at least one line of pre- and post-context; add more if helpful. Be conservative, do not include whole file.
- Use @@ anchor to separate multiple changes within a single file:
  @@
- If insufficient to disambiguate, add an @@ anchor naming the class or function:
  @@ class BaseClass
  @@     def method_name(...):

Change lines:
- Use '-' for the old line, '+' for the new line.
- The text after the sign must be exact (including whitespace).

Rules:

1. Literal text only. Emit the file's exact bytes as text lines.
 * Do not add Markdown/JSON escaping.
 * Preserve quotes, backslashes, tabs, Unicode, and blank lines exactly.

2. Line prefixes:
 * Non-blank context lines start with one leading space followed by the exact text.
 * Change lines start with - (old) or + (new) followed by the exact text.
 * A blank context line is completely empty (no spaces).

3. Context must match the current file character for character.
 * Provide at least one line of pre- and post-context when updating. Add up to 3 lines if it helps disambiguate.

4. Ordering & uniqueness:
 * Include each file path once. Merge all changes for that file into its single section.
 * Within a file, order context blocks top-to-bottom by their occurrence in the file.
 * For Update sections, if moving/renaming the file, add a line `*** Move to: <relative/new/path>` directly after the Update header and before any changes.
 * For multiple patches, make sure they're all added to a single fenced patch envelope.

5. Newlines:
 * Preserve each line’s trailing newline semantics.
 * If the file ends without a trailing newline, represent the last line exactly as it exists (no extra newline).

7. Tabs & spaces: Preserve indentation exactly; do not convert tabs to spaces or vice versa.

## Minimal example:
```patch
*** Begin Patch
*** Update File: pkg/mod.py
 header1

-old
+new
 footer1
*** Update File: pkg/foo.txt
@@
 test
-foo
+bar
 test
*** Delete File: scripts/old_tool.py
*** End Patch
```

## Escapes (emit literally, never double-escape):
Source contains assert s == "a\\b\n"

```patch
*** Begin Patch
*** Update File: src/checks.py
     def test_str():
-        assert s == "a\\b\n"
+        assert s == "a\\c\n"
*** End Patch
```

# Self-Check (must pass before you output)

* Exactly one fenced code block labeled patch, nothing else.
* Envelope lines are present and exact: *** Begin Patch … *** End Patch.
* Each file path appears once; Add/Update/Delete headers are correct.
* For Update sections: at least one pre- and post-context line; context matches file exactly.
* No JSON/Markdown escaping added; quotes/backslashes/tabs preserved literally.
* Blank context lines are truly empty; non-blank context lines start with one space.
* Multiple edits within a file are separated by @@ (and optional labeled anchors if needed).
* No trailing commentary or extra fences outside the single patch block.
"""


class DiffError(ValueError):
    """Any problem detected while parsing or applying a patch."""


class ActionType(Enum):
    ADD = auto()
    UPDATE = auto()
    DELETE = auto()


class NeedleType(Enum):
    ANCHOR = auto()
    CONTEXT = auto()
    DELETE = auto()


@dataclass
class NeedleItem:
    type: NeedleType
    text: str


@dataclass
class EditGroup:
    # Position in the CONTEXT/DELETE-only pattern where insert should happen.
    # For del_count > 0, insertion occurs immediately after the last delete.
    # For del_count == 0, insertion occurs at this position without consuming any file lines.
    start_pat_index: int
    del_count: int = 0
    additions: List[str] = field(default_factory=list)


@dataclass
class Chunk:
    items: List[NeedleItem] = field(default_factory=list)
    edits: List[EditGroup] = field(default_factory=list)
    start_line: Optional[int] = None


@dataclass
class PatchAction:
    type: ActionType = ActionType.UPDATE
    chunks: List[Chunk] = field(default_factory=list)
    move_path: Optional[str] = None


@dataclass
class Patch:
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
    move_path: Optional[str] = None


@dataclass
class Commit:
    changes: Dict[str, FileChange] = field(default_factory=dict)


BEGIN_MARKER = "*** Begin Patch"
END_MARKER = "*** End Patch"
FILE_HEADER_RE = re.compile(r"^\*\*\*\s+(Add|Update|Delete)\s+File:\s+(.+)$")
ANCHOR_RE = re.compile(r"^@@(.*)$")
ANCHOR_PREFIX = "@@"
DELETE_PREFIX = "-"
ADD_PREFIX = "+"
CONTEXT_PREFIX = " "
MOVE_TO_PREFIX = "*** Move to:"


# Partial/best match diagnostics removed; unmatched blocks will be quoted in hints.
def render_chunk_block(ch: Chunk) -> str:
    """
    Render a chunk in a diff-like quoted form:
    - Anchors with '@@'
    - Context with a single leading space
    - Deletions with '-'
    - Additions with '+'
    """
    out: List[str] = []
    # Emit anchors as provided
    for it in ch.items:
        if it.type == NeedleType.ANCHOR:
            anchor_txt = it.text
            out.append(f"{ANCHOR_PREFIX} {anchor_txt}".rstrip())
    # Build the pattern (excluding anchors) to position additions
    pat_built: List[tuple[NeedleType, str]] = [
        (it.type, it.text) for it in ch.items if it.type != NeedleType.ANCHOR
    ]
    pat_len_now = len(pat_built)
    emitted: Dict[int, bool] = {}
    for jj, (lt2, t2) in enumerate(pat_built):
        # Insert zero-delete additions at this position
        for gi, g in enumerate(ch.edits):
            if g.del_count == 0 and g.start_pat_index == jj and not emitted.get(gi, False):
                for a in g.additions:
                    out.append(f"{ADD_PREFIX}{a}")
                emitted[gi] = True
        if lt2 == NeedleType.CONTEXT:
            out.append(f"{CONTEXT_PREFIX}{t2}")
        elif lt2 == NeedleType.DELETE:
            out.append(f"{DELETE_PREFIX}{t2}")
            # For deletions, emit additions after the last deleted line in the group
            for gi, g in enumerate(ch.edits):
                if g.del_count > 0 and g.start_pat_index <= jj < g.start_pat_index + g.del_count:
                    if jj == g.start_pat_index + g.del_count - 1 and not emitted.get(gi, False):
                        for a in g.additions:
                            out.append(f"{ADD_PREFIX}{a}")
                        emitted[gi] = True
    # Tail insertions at end
    for gi, g in enumerate(ch.edits):
        if g.start_pat_index == pat_len_now and not emitted.get(gi, False):
            for a in g.additions:
                out.append(f"{ADD_PREFIX}{a}")
            emitted[gi] = True
    return "\n".join(out)


def _is_relative_path(p: str) -> bool:
    # Reject absolute POSIX and Windows (drive or UNC) paths
    if not p:
        return False
    if p.startswith("/") or p.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", p):
        return False
    # Normalize and ensure it doesn't escape upward with absolute root
    norm = os.path.normpath(p)
    # Keep relative; allow ../ segments, but forbid path becoming absolute after norm
    return not os.path.isabs(norm)


def parse_v4a_patch(text: str) -> Tuple[Patch, List[PatchError]]:
    """
    Best-effort, resilient V4A parser.
    - Ignores any text before the first BEGIN marker and after the matching END marker.
    - Validates:
        * One BEGIN and one END marker (records errors if multiples).
        * Each file appears exactly once (duplicates are errors; later ones ignored).
        * Paths must be relative.
        * Delete sections must not contain change/content lines.
        * Context chunk boundaries must be unambiguous (require @@ between multiple chunks).
        * Context chunks may use any number of prefix/suffix lines (3 is typical).
    Returns a Patch model and a list of PatchError.
    """
    errors: List[PatchError] = []
    lines = text.splitlines()

    begin_idxs = [i for i, l in enumerate(lines) if l.strip() == BEGIN_MARKER]
    end_idxs = [i for i, l in enumerate(lines) if l.strip() == END_MARKER]

    def add_error(
        msg: str,
        *,
        line: Optional[int] = None,
        hint: Optional[str] = None,
        filename: Optional[str] = None,
    ):
        errors.append(PatchError(msg=msg, line=line, hint=hint, filename=filename))

    if not begin_idxs:
        return Patch(), [
            PatchError(
                "Missing *** Begin Patch",
                line=None,
                hint="Ensure patch is wrapped with *** Begin Patch / *** End Patch",
            )
        ]

    if not end_idxs:
        return Patch(), [
            PatchError(
                "Missing *** End Patch",
                line=None,
                hint="Ensure patch is wrapped with *** Begin Patch / *** End Patch",
            )
        ]

    if len(begin_idxs) > 1:
        extra_begins = [str(i + 1) for i in begin_idxs[1:]]
        return Patch(), [
            PatchError(
                msg="Multiple *** Begin Patch markers found",
                line=begin_idxs[1] + 1,
                hint=(
                    "Merge all changes into a single fenced patch block enclosed by one "
                    "*** Begin Patch and one *** End Patch. "
                    f"Extra BEGIN markers at lines: {', '.join(extra_begins)}"
                ),
            )
        ]

    # Choose the first end after the first begin; if none, error
    first_begin = begin_idxs[0]
    ends_after_begin = [i for i in end_idxs if i > first_begin]
    if not ends_after_begin:
        add_error(
            "No *** End Patch after *** Begin Patch",
            line=first_begin + 1,
            hint="Add *** End Patch after this line",
        )
        return Patch(), errors
    if len(end_idxs) > 1:
        first_end_after_begin = ends_after_begin[0]
        extras = [i for i in end_idxs if i != first_end_after_begin]
        add_error(
            "Multiple *** End Patch markers found; using the first after begin",
            line=(extras[0] + 1) if extras else (first_end_after_begin + 1),
            hint=f"Extra END markers at lines: {', '.join(str(i + 1) for i in extras)}",
        )
    first_end = ends_after_begin[0]

    content = lines[first_begin + 1 : first_end]

    patch = Patch()

    current_path: Optional[str] = None
    current_action: Optional[PatchAction] = None
    skip_current_file: bool = False

    # Chunk assembly state
    pending_anchors: List[str] = []
    current_chunk: Optional[Chunk] = None
    chunk_has_mods: bool = False  # any +/- seen in current_chunk
    seen_ctx_after_mods: bool = False
    current_group: Optional[EditGroup] = None
    # Number of CONTEXT/DELETE items already added to the current chunk (used for start_pat_index)
    pat_index_in_chunk: int = 0

    def _append_anchor_items(chunk: Chunk) -> None:
        nonlocal pending_anchors
        if pending_anchors:
            for a in pending_anchors:
                chunk.items.append(NeedleItem(NeedleType.ANCHOR, a))
            pending_anchors = []

    def finish_chunk_if_any():
        nonlocal current_chunk, chunk_has_mods, pending_anchors, seen_ctx_after_mods, current_group, pat_index_in_chunk
        if current_chunk is None:
            pending_anchors = []
            return
        has_context = any(it.type == NeedleType.CONTEXT for it in current_chunk.items)
        if not chunk_has_mods:
            # Special case: for Add sections, treat context-only block as file content
            if (
                current_action is not None
                and current_action.type == ActionType.ADD
                and has_context
            ):
                # Convert all context lines into a single edit group of additions
                additions = [
                    it.text
                    for it in current_chunk.items
                    if it.type == NeedleType.CONTEXT
                ]
                current_chunk.edits.append(
                    EditGroup(start_pat_index=0, del_count=0, additions=additions)
                )
                current_action.chunks.append(current_chunk)
            # Otherwise, ignore empty chunk for Update (no +/- lines)
        else:
            # Add action must not include context when there are +/- lines
            if (
                current_action is not None
                and current_action.type == ActionType.ADD
                and has_context
            ):
                add_error(
                    f"Add file section for {current_path} must not contain context",
                    line=current_chunk.start_line,
                    hint="Remove context lines for Add sections; only use + lines",
                    filename=current_path,
                )
            if current_action is not None:
                current_action.chunks.append(current_chunk)
        current_chunk = None
        chunk_has_mods = False
        seen_ctx_after_mods = False
        current_group = None
        pat_index_in_chunk = 0

    def start_chunk_if_needed():
        nonlocal current_chunk, chunk_has_mods, pending_anchors, seen_ctx_after_mods, current_group, pat_index_in_chunk
        if current_chunk is None:
            current_chunk = Chunk(items=[], edits=[], start_line=current_line_num)
            _append_anchor_items(current_chunk)
            chunk_has_mods = False
            seen_ctx_after_mods = False
            current_group = None
            pat_index_in_chunk = 0

    for current_line_num, raw_line in enumerate(content, start=first_begin + 2):
        # Detect new file header
        m_header = FILE_HEADER_RE.match(raw_line)
        if m_header is not None:
            # Close previous file and chunk
            finish_chunk_if_any()
            current_chunk = None
            chunk_has_mods = False
            pending_anchors = []

            action_word, path = m_header.group(1), m_header.group(2).strip()
            current_path = path
            current_action = None
            skip_current_file = False

            # Validate path
            if not _is_relative_path(path):
                add_error(
                    f"Path must be relative: {path!r}",
                    line=current_line_num,
                    hint="Use a relative path, not absolute",
                    filename=path,
                )
                skip_current_file = True
                continue

            # Initialize action
            if action_word == "Add":
                current_action = PatchAction(type=ActionType.ADD)
            elif action_word == "Update":
                current_action = PatchAction(type=ActionType.UPDATE)
            elif action_word == "Delete":
                current_action = PatchAction(type=ActionType.DELETE)
            else:
                add_error(
                    f"Unknown action '{action_word}' for file {path}",
                    line=current_line_num,
                    hint="Use Add/Update/Delete",
                    filename=path,
                )
                skip_current_file = True
                continue

            # Record action
            patch.actions.setdefault(path, []).append(current_action)
            continue

        # Ignore anything until we have a current file
        if current_path is None or current_action is None or skip_current_file:
            continue

        # Handle optional Move for Update: only allowed before any chunk content
        if raw_line.startswith(MOVE_TO_PREFIX):
            move_to = raw_line[len(MOVE_TO_PREFIX) :].strip()
            if current_action.type != ActionType.UPDATE:
                add_error(
                    f"Move directive is only valid in Update sections: {current_path}",
                    line=current_line_num,
                    filename=current_path,
                )
            elif current_chunk is not None or current_action.chunks:
                add_error(
                    f"Move directive must appear before any change blocks in {current_path}",
                    line=current_line_num,
                    filename=current_path,
                )
            elif current_action.move_path is not None:
                add_error(
                    f"Duplicate Move directive in {current_path}",
                    line=current_line_num,
                    filename=current_path,
                )
            elif not _is_relative_path(move_to):
                add_error(
                    f"Path must be relative: {move_to!r}",
                    line=current_line_num,
                    hint="Use a relative path for Move to",
                    filename=current_path,
                )
            else:
                current_action.move_path = move_to
            continue

        # Handle Delete action: it must not have content
        if current_action.type == ActionType.DELETE:
            # No anchors, +/- or any other non-empty content allowed
            if raw_line.strip() != "":
                add_error(
                    f"Delete file section for {current_path} must not contain changes or content",
                    line=current_line_num,
                    hint="Delete sections must not include anchors, +/- lines, or context/content",
                    filename=current_path,
                )
            continue

        # Non-delete: Update/Add have content blocks
        if raw_line.startswith(ANCHOR_PREFIX):
            # Treat @@ as a chunk breaker; collect anchors to be placed at the top of the next chunk.
            finish_chunk_if_any()
            anchor_text = raw_line[len(ANCHOR_PREFIX) :]
            if anchor_text.startswith(" "):
                anchor_text = anchor_text[1:]
            pending_anchors.append(anchor_text)
            continue

        # Diff lines
        if raw_line.startswith(DELETE_PREFIX) or raw_line.startswith(ADD_PREFIX):
            # Allow interleaved +/- groups within a single chunk (no @@ required).
            start_chunk_if_needed()

            if raw_line.startswith(DELETE_PREFIX):
                # Open/continue current edit group for deletions
                if current_group is None:
                    current_group = EditGroup(
                        start_pat_index=pat_index_in_chunk, del_count=0, additions=[]
                    )
                    current_chunk.edits.append(current_group)
                current_chunk.items.append(
                    NeedleItem(NeedleType.DELETE, raw_line[len(DELETE_PREFIX) :])
                )
                current_group.del_count += 1
                pat_index_in_chunk += 1
                chunk_has_mods = True
            else:
                # Addition belongs to the current edit group; if none, create an insertion-only group.
                if current_group is None:
                    current_group = EditGroup(
                        start_pat_index=pat_index_in_chunk, del_count=0, additions=[]
                    )
                    current_chunk.edits.append(current_group)
                current_group.additions.append(raw_line[len(ADD_PREFIX) :])
                chunk_has_mods = True
            continue

        # Context line or invalid starter
        # Allow blank lines as empty context; otherwise require a single leading space.
        if raw_line.strip() == "":
            # Blank line -> empty context
            start_chunk_if_needed()
            ctx_line = ""
            current_chunk.items.append(NeedleItem(NeedleType.CONTEXT, ctx_line))
            pat_index_in_chunk += 1
            # Seeing context separates edit groups; next +/- opens a new group.
            current_group = None
            if chunk_has_mods:
                seen_ctx_after_mods = True
            continue
        if raw_line.startswith(CONTEXT_PREFIX):
            start_chunk_if_needed()
            ctx_line = raw_line[len(CONTEXT_PREFIX) :]
            current_chunk.items.append(NeedleItem(NeedleType.CONTEXT, ctx_line))
            pat_index_in_chunk += 1
            # Seeing context separates edit groups; next +/- opens a new group.
            current_group = None
            if chunk_has_mods:
                seen_ctx_after_mods = True
            continue
        # Any other non-blank line is invalid inside a file section
        # For Add sections, treat such lines as implicit additions to be permissive.
        if current_action.type == ActionType.ADD:
            start_chunk_if_needed()
            # Treat as file content to add
            if current_group is None:
                current_group = EditGroup(
                    start_pat_index=pat_index_in_chunk, del_count=0, additions=[]
                )
                current_chunk.edits.append(current_group)
            current_group.additions.append(raw_line)
            chunk_has_mods = True
            continue

        add_error(
            f"Invalid patch line in {current_path}: must start with @@, -, +, or a space",
            line=current_line_num,
            hint="Lines inside file sections must start with '@@', '-', '+', or a single leading space for context. Blank lines are allowed as empty context.",
            filename=current_path,
        )
        # Skip this line and continue parsing
        continue

    # End of content: close out any open chunk
    finish_chunk_if_any()

    return patch, errors


def load_files(
    paths: List[str], open_fn: Callable[[str], str]
) -> Tuple[Dict[str, str], List[PatchError]]:
    """
    Read a set of files, returning a (files, errors) tuple.
    files: mapping path -> content for successfully read files
    errors: list of PatchError for files that failed to read
    """
    files: Dict[str, str] = {}
    errs: List[PatchError] = []
    for path in paths:
        try:
            files[path] = open_fn(path)
        except Exception as e:
            errs.append(
                PatchError(
                    msg=f"Failed to read file: {path}",
                    line=None,
                    hint=f"{type(e).__name__}: {e}",
                    filename=path,
                )
            )
    return files, errs


def build_commits(
    patch: Patch, files: Dict[str, str]
) -> Tuple[List["Commit"], List[PatchError], Dict[str, FileApplyStatus]]:
    """
    Produce a list of Commit objects describing concrete file changes
    from a parsed Patch and a mapping of already loaded file contents.
    Returns (commits, errors).
    """
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

    # Preprocess and flatten actions to resolve conflicts and merge updates.
    effective_actions: Dict[str, PatchAction] = {}
    for path, actions in patch.actions.items():
        num_creates = sum(1 for a in actions if a.type == ActionType.ADD)
        num_deletes = sum(1 for a in actions if a.type == ActionType.DELETE)

        if num_creates > 1:
            add_error(f"Multiple Add File sections for {path}", filename=path)
            continue
        if num_deletes > 1:
            add_error(f"Multiple Delete File sections for {path}", filename=path)
            continue

        has_create = num_creates == 1
        has_delete = num_deletes == 1
        has_update = any(a.type == ActionType.UPDATE for a in actions)

        if has_create:
            create_action = next(a for a in actions if a.type == ActionType.ADD)
            if has_update:
                add_error(f"Cannot mix Update and Add sections for {path}", filename=path)
                continue
            if has_delete:
                delete_action = next(a for a in actions if a.type == ActionType.DELETE)
                if actions.index(delete_action) > actions.index(create_action):
                    add_error(f"Add must follow Delete for {path}", filename=path)
                    continue
            effective_actions[path] = create_action
        elif has_delete:
            if has_update:
                add_error(f"Cannot mix Delete and Update sections for {path}", filename=path)
                continue
            effective_actions[path] = next(a for a in actions if a.type == ActionType.DELETE)
        elif has_update:
            update_actions = [a for a in actions if a.type == ActionType.UPDATE]
            merged_chunks = []
            for ua in update_actions:
                merged_chunks.extend(ua.chunks)

            last_move_path = None
            for ua in reversed(update_actions):
                if ua.move_path:
                    last_move_path = ua.move_path
                    break

            effective_actions[path] = PatchAction(
                type=ActionType.UPDATE,
                chunks=merged_chunks,
                move_path=last_move_path,
            )

    if errors:
        return [], errors, {}

    def join_lines(lines: List[str], *, eol: bool) -> str:
        s = "\n".join(lines)
        return s + ("\n" if eol else "")

    def format_block_excerpt(s: int, e: int) -> str:
        max_lines = 8
        segment = lines[s:e]
        if len(segment) > max_lines:
            head = segment[: max_lines // 2]
            tail = segment[-(max_lines // 2) :]
            segment = [*head, "...", *tail]
        return "\n".join(f"  | {ln}" for ln in segment)

    def find_chunk_linear(
        file_lines: List[str],
        chunk: Chunk,
        start_min: int = 0,
    ) -> Tuple[Optional[int], Optional[int], Optional[List[str]], Optional[str]]:
        """
        Linear, anchor-aware search for a chunk.
        Anchors act as forward skips (labeled anchors advance to next line containing the label).
        Pattern is the sequence of CONTEXT and DELETE items; additions are not part of the needle.
        Fuzzy matching allows insertion of empty CONTEXT lines when file contains additional blanks.
        Returns (start_idx, end_idx, replacement_lines, hint) where replacement_lines is the
        buffer to substitute for the matched pattern region (i.e. with additions applied).
        """
        # Build pattern from items (exclude anchors)
        items = chunk.items
        pat_items_idx: List[int] = []
        pat_types: List[NeedleType] = []
        pat_texts: List[str] = []
        for idx, it in enumerate(items):
            if it.type == NeedleType.ANCHOR:
                continue
            pat_items_idx.append(idx)
            pat_types.append(it.type)
            pat_texts.append(it.text)
        pat_len = len(pat_texts)
        n_lines = len(file_lines)
        if pat_len == 0:
            return None, None, None, "Empty change block (no context/deletions)"

        # Candidate starts: consider all feasible positions, honoring a minimum start index
        start_min = max(0, start_min)
        candidate_starts: List[int] = list(range(start_min, max(start_min, n_lines - pat_len + 1)))

        anchor_idxs: List[int] = []
        for it in items:
            if it.type == NeedleType.ANCHOR and it.text:
                for i, line in enumerate(file_lines):
                    if it.text in line:
                        anchor_idxs.append(i)
        if anchor_idxs:
            # Start matching at or after the earliest labeled anchor occurrence,
            # also honoring the provided start_min lower bound.
            min_anchor = min(anchor_idxs)
            lower_bound = max(start_min, min_anchor)
            candidate_starts = [i for i in candidate_starts if i >= lower_bound]
        # Partial/best match tracking removed.

        def try_match_at(
            start: int,
        ) -> Tuple[
            bool, int, Optional[str], List[int], Optional[List[str]], Optional[int]
        ]:
            i = start
            j = 0
            matched = 0
            prev_was_context: Optional[bool] = None
            insert_pat_positions: List[int] = []

            while j < pat_len:
                if i >= n_lines:
                    expected = pat_texts[j]
                    return False, matched, None, insert_pat_positions, None, None

                expected_line = pat_texts[j]
                actual_line = file_lines[i]
                lt = pat_types[j]

                if actual_line == expected_line:
                    matched += 1
                    prev_was_context = lt == NeedleType.CONTEXT
                    i += 1
                    j += 1
                    continue

                # Fuzzy: treat an empty file line as an inserted empty CONTEXT where reasonable
                if actual_line == "":
                    # Missing blank before a non-empty CONTEXT line
                    if lt == NeedleType.CONTEXT and expected_line != "":
                        insert_pat_positions.append(
                            j
                        )  # insert before current pattern position
                        i += 1
                        prev_was_context = True
                        continue
                    # Extra blank at deletion boundary (immediately after a context match)
                    if lt == NeedleType.DELETE and prev_was_context is not None:
                        insert_pat_positions.append(
                            j
                        )  # insert a blank CONTEXT at this boundary
                        i += 1
                        continue

                return False, matched, None, insert_pat_positions, None, None

            # Success: mutate chunk items to include inserted blanks at the recorded pat indices
            if insert_pat_positions:
                # Map pat index -> items index
                items_idx_for_pat = list(pat_items_idx)
                for pos in sorted(insert_pat_positions):
                    insert_at_items_idx = items_idx_for_pat[pos]
                    chunk.items.insert(
                        insert_at_items_idx, NeedleItem(NeedleType.CONTEXT, "")
                    )
                    # When we insert, subsequent item indices shift by +1 for >= insert_at_items_idx
                    for k in range(pos, len(items_idx_for_pat)):
                        items_idx_for_pat[k] += 1
                    # Also shift edit groups whose start index is at or after this insertion
                    for g in chunk.edits:
                        if g.start_pat_index >= pos:
                            g.start_pat_index += 1
            # Build replacement buffer now that we have a complete match
            pat_built: List[tuple[NeedleType, str]] = [
                (it.type, it.text) for it in chunk.items if it.type != NeedleType.ANCHOR
            ]
            replacement: List[str] = []
            group_progress: Dict[int, int] = {}
            inserted_group: Dict[int, bool] = {}
            k = start
            for jj, (lt2, _t2) in enumerate(pat_built):
                # Insert zero-delete additions at this position
                for gi, g in enumerate(chunk.edits):
                    if (
                        g.del_count == 0
                        and g.start_pat_index == jj
                        and not inserted_group.get(gi, False)
                    ):
                        replacement.extend(g.additions)
                        inserted_group[gi] = True
                if lt2 == NeedleType.CONTEXT:
                    replacement.append(file_lines[k])
                    k += 1
                elif lt2 == NeedleType.DELETE:
                    k += 1
                    for gi, g in enumerate(chunk.edits):
                        if (
                            g.del_count > 0
                            and g.start_pat_index
                            <= jj
                            < g.start_pat_index + g.del_count
                        ):
                            group_progress[gi] = group_progress.get(gi, 0) + 1
                            if group_progress[
                                gi
                            ] == g.del_count and not inserted_group.get(gi, False):
                                replacement.extend(g.additions)
                                inserted_group[gi] = True
            # Tail insertions (groups at end)
            pat_len_now = len(pat_built)
            for gi, g in enumerate(chunk.edits):
                if g.start_pat_index == pat_len_now and not inserted_group.get(
                    gi, False
                ):
                    replacement.extend(g.additions)
                    inserted_group[gi] = True
            end_idx = start + len(pat_built)
            return True, matched, None, [], replacement, end_idx

        for start in candidate_starts:
            ok, matched, _hint, _ins, replacement, end_idx = try_match_at(start)
            if ok:
                return start, end_idx, replacement, None

        # Failure: quote the unmatched block the user provided
        block_quote = render_chunk_block(chunk)
        hint = f"Change block not found. Here is the block you provided:\n---\n{block_quote}\n---"
        return None, None, None, hint

    # Process actions (compute all matches first per file, then check overlaps, then apply)
    for path, action in effective_actions.items():
        # Guard: Update sections must include '+' and/or '-' change lines.
        # Context-only chunks in Update are ignored during parsing, which otherwise makes the file silently no-op.
        if action.type == ActionType.UPDATE and not action.chunks:
            add_error(
                f"No change lines (+/-) provided for file: {path}",
                hint=(
                    "Update sections must include '-' for removed lines and '+' for added lines, "
                    "with surrounding context lines that start with a single space. "
                    "Pure context blocks are ignored for Update."
                ),
                filename=path,
            )
            # Skip this file; other files can still be applied.
            continue

        if action.type == ActionType.ADD:
            # Build content from additions across all chunks
            add_lines: List[str] = []
            for ch in action.chunks:
                for g in ch.edits:
                    add_lines.extend(g.additions)
            new_content = join_lines(add_lines, eol=False)
            changes[path] = FileChange(type=ActionType.ADD, new_content=new_content)
            status_map[path] = FileApplyStatus.Create
            continue

        if action.type == ActionType.DELETE:
            changes[path] = FileChange(type=ActionType.DELETE)
            status_map[path] = FileApplyStatus.Delete
            continue

        # UPDATE
        original = files.get(path)
        if original is None:
            add_error(
                f"No loaded content for file: {path}",
                hint="Load files before building commits or ensure the file exists for update.",
                filename=path,
            )
            continue
        # Preserve trailing newline status
        had_eol = original.endswith("\n")
        lines = original.splitlines()

        # Phase 1: locate all blocks for this file
        located: List[Tuple[int, int, List[str], Optional[int]]] = []
        any_failed = False
        last_start_idx: Optional[int] = None
        last_end_idx: Optional[int] = None
        for ch in action.chunks:
            # First, attempt a global search (for order/overlap diagnostics).
            start_idx, end_idx, replacement, hint = find_chunk_linear(lines, ch, start_min=0)
            if start_idx is None or end_idx is None or replacement is None:
                add_error(
                    f"Failed to locate change block in {path}",
                    line=ch.start_line,
                    hint=hint,
                    filename=path,
                )
                any_failed = True
                # Do not break; collect all failures for better reporting
                continue
            # If this match would overlap the previous match, attempt to find a non-overlapping
            # occurrence starting at the end of the previous match. This disambiguates duplicate
            # identical blocks that legitimately occur twice.
            if last_end_idx is not None and start_idx < last_end_idx:
                start2, end2, replacement2, _hint2 = find_chunk_linear(
                    lines, ch, start_min=last_end_idx
                )
                if start2 is not None and end2 is not None and replacement2 is not None:
                    start_idx, end_idx, replacement = start2, end2, replacement2
            # Order check: ensure non-decreasing positions (after any overlap disambiguation)
            if last_start_idx is not None and start_idx < last_start_idx:
                add_error(
                    f"Out-of-order change block in {path}",
                    line=ch.start_line,
                    hint="Ensure blocks are ordered top-to-bottom as they appear in the file, or add @@ anchors to disambiguate.",
                    filename=path,
                )
                any_failed = True
                continue
            last_start_idx = start_idx
            last_end_idx = end_idx
            located.append((start_idx, end_idx, replacement, ch.start_line))

        # If some chunks could not be found, still apply the ones we did find.
        # If none located, skip updating this file.
        if not located:
            continue

        # Secondary order check: ensure start indices are non-decreasing in parse order.
        starts_in_parse_order = [s for (s, _e, _r, _lno) in located]
        if any(
            starts_in_parse_order[i] > starts_in_parse_order[i + 1]
            for i in range(len(starts_in_parse_order) - 1)
        ):
            add_error(
                f"Out-of-order change block in {path}",
                line=None,
                hint="Ensure blocks are ordered top-to-bottom as they appear in the file, or add @@ anchors to disambiguate.",
                filename=path,
            )

        # Phase 2: detect overlaps (using original file indices)
        located.sort(key=lambda t: t[0])
        overlaps_found = False
        for (s1, e1, _r1, l1), (s2, e2, _r2, l2) in zip(located, located[1:]):
            if not (e1 <= s2):  # overlap if e1 > s2
                overlaps_found = True
                overlap_hint = (
                    "Two change blocks overlap in their context/deletion ranges. "
                    f"First block covers [{s1}, {e1}), second covers [{s2}, {e2}).\n"
                    "First block excerpt:\n"
                    f"{format_block_excerpt(s1, e1)}\n"
                    "Second block excerpt:\n"
                    f"{format_block_excerpt(s2, e2)}\n"
                    "Reorder the chunks or regenerate the patch to avoid overlapping contexts."
                )
                add_error(
                    f"Overlapping change blocks detected in {path}",
                    line=min((l1 or 0), (l2 or 0)) or None,
                    hint=overlap_hint,
                    filename=path,
                )
        if overlaps_found:
            # Do not apply any changes to this file if overlaps exist
            continue

        # Phase 3: apply non-overlapping blocks to produce new content
        result: List[str] = []
        cursor = 0
        for start_idx, end_idx, replacement, _lno in located:
            # Copy up to the block start
            result.extend(lines[cursor:start_idx])
            # Use precomputed replacement buffer
            result.extend(replacement)
            cursor = end_idx
        # Append remainder of file
        result.extend(lines[cursor:])
        lines = result

        new_content = join_lines(lines, eol=had_eol)
        changes[path] = FileChange(
            type=ActionType.UPDATE,
            old_content=original,
            new_content=new_content,
            move_path=action.move_path,
        )
        # Mark status based on whether any chunks for this file failed to locate
        status_map[path] = (
            FileApplyStatus.PartialUpdate if any_failed else FileApplyStatus.Update
        )

    if changes:
        commits.append(Commit(changes=changes))

    return commits, errors, status_map


def apply_commits(
    commits: List[Commit],
    write_fn: Callable[[str, str], None],
    delete_fn: Callable[[str], None],
) -> List[PatchError]:
    """
    Apply a list of commits using provided IO functions.
    Always attempts all writes/deletes, collecting errors for any failures.
    """
    errors: List[PatchError] = []

    for commit in commits:
        for path, change in commit.changes.items():
            try:
                if change.type == ActionType.ADD or change.type == ActionType.UPDATE:
                    target = change.move_path or path
                    write_fn(target, change.new_content or "")
                    if change.type == ActionType.UPDATE and change.move_path:
                        delete_fn(path)
                elif change.type == ActionType.DELETE:
                    delete_fn(path)
                else:
                    # Future-proof: unknown types
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
    patch, errors = parse_v4a_patch(text)
    if errors:
        # Enrich specific parse-time errors with a fenced source snippet of the target file.
        for e in errors:
            if (
                e.msg.startswith("Invalid patch line in ")
                and e.filename is not None
            ):
                try:
                    src = open_fn(e.filename)
                    fence = "```"
                    snippet = f"{fence}\n{src}\n{fence}"
                    e.hint = f"{e.hint}\n\n{snippet}" if e.hint else snippet
                except Exception:
                    # If we cannot read the file, leave the original hint as-is.
                    pass
        return {}, errors

    # Load files needed for application (Update only). Add/Delete do not require reading here.
    paths = [
        p
        for p, actions in patch.actions.items()
        if any(a.type == ActionType.UPDATE for a in actions)
    ]
    files, read_errors = load_files(paths, open_fn)
    if read_errors:
        return {}, read_errors

    # Build commits (may be partial) and apply; return combined errors with statuses.
    commits, build_errors, status_map = build_commits(patch, files)
    apply_errors = apply_commits(commits, write_fn, delete_fn)
    return status_map, [*build_errors, *apply_errors]
