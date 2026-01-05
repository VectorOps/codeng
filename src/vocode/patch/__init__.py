from __future__ import annotations

import pathlib
from typing import Callable, Dict, List, Tuple, Optional
from abc import ABC, abstractmethod

from .models import FileApplyStatus
from .v4a import (
    process_patch as v4a_process_patch,
    DIFF_SYSTEM_INSTRUCTION as V4A_SYSTEM_INSTRUCTION,
)
from .patch import (
    process_patch as sr_process_patch,
    DIFF_PATCH_SYSTEM_INSTRUCTION as PATCH_SYSTEM_INSTRUCTION,
)
from .v4a import DiffError

# Internal registry of supported patch formats
_REGISTRY: Dict[str, Dict[str, object]] = {
    "v4a": {
        "handler": v4a_process_patch,
        "system_prompt": V4A_SYSTEM_INSTRUCTION,
    },
    "patch": {
        "handler": sr_process_patch,
        "system_prompt": PATCH_SYSTEM_INSTRUCTION,
    },
}


def get_supported_formats() -> Tuple[str, ...]:
    return tuple(_REGISTRY.keys())


def get_system_instruction(fmt: str) -> str:
    key = (fmt or "").lower()
    entry = _REGISTRY.get(key)
    if not entry:
        raise ValueError(f"Unsupported patch format: {fmt}")
    return entry["system_prompt"]  # type: ignore[return-value]


class PatchFileOps(ABC):
    """
    Abstract contract for file operations used by patch processors.
    Implementations must handle path safety and track changes map.
    """

    @abstractmethod
    def open(self, rel: str) -> str: ...

    @abstractmethod
    def write(self, rel: str, content: str) -> None: ...

    @abstractmethod
    def delete(self, rel: str) -> None: ...

    @property
    @abstractmethod
    def changes_map(self) -> Dict[str, str]:
        """
        A map of relative file paths to change kind: 'created' | 'updated' | 'deleted'.
        """
        ...


class FileSystemPatchFileOps(PatchFileOps):
    """
    File-backed implementation that enforces path safety under base_path and
    records change kinds for refresh.
    """

    def __init__(self, base_path: pathlib.Path):
        self._base_path = base_path
        self._changes: Dict[str, str] = {}

    def _resolve_safe_path(self, rel: str) -> pathlib.Path:
        if rel.startswith("/") or rel.startswith("~"):
            raise DiffError(f"Absolute paths are not allowed: {rel}")
        abs_path = (self._base_path / rel).resolve()
        base_resolved = self._base_path.resolve()
        if str(abs_path).startswith(str(base_resolved)):
            return abs_path
        raise DiffError(f"Path escapes project root: {rel}")

    def _record(self, rel: str, change: str) -> None:
        prev = self._changes.get(rel)
        if prev is None:
            self._changes[rel] = change
            return
        if change == "deleted":
            self._changes[rel] = change
        elif change == "updated" and prev != "deleted":
            self._changes[rel] = change

    def open(self, rel: str) -> str:
        path = self._resolve_safe_path(rel)
        with path.open("rt", encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel: str, content: str) -> None:
        path = self._resolve_safe_path(rel)
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wt", encoding="utf-8") as fh:
            fh.write(content)
        self._record(rel, "updated" if existed else "created")

    def delete(self, rel: str) -> None:
        path = self._resolve_safe_path(rel)
        path.unlink(missing_ok=True)
        self._record(rel, "deleted")

    @property
    def changes_map(self) -> Dict[str, str]:
        return self._changes


def apply_patch(
    fmt: str,
    text: str,
    base_path: pathlib.Path,
    ops: Optional[PatchFileOps] = None,
) -> Tuple[str, str, Dict[str, str], Dict[str, FileApplyStatus], List[object]]:
    """
    Apply a patch using the specified format ('v4a' or 'patch').
    If ops is not provided, a file-backed implementation under base_path is used.
    Returns (summary_text, outcome_name, changes_map, status_map, errors).
    changes_map values: 'created' | 'updated' | 'deleted'
    """
    key = (fmt or "").lower()
    entry = _REGISTRY.get(key)
    if not entry:
        raise ValueError(f"Unsupported patch format: {fmt}")
    handler = entry["handler"]  # type: ignore[assignment]
    file_ops = ops or FileSystemPatchFileOps(base_path)

    # Process
    statuses, errs = handler(  # type: ignore[misc]
        text, file_ops.open, file_ops.write, file_ops.delete
    )

    # Summarize
    created = sorted([f for f, s in statuses.items() if s == FileApplyStatus.Create])
    updated_full = sorted(
        [f for f, s in statuses.items() if s == FileApplyStatus.Update]
    )
    updated_partial = sorted(
        [f for f, s in statuses.items() if s == FileApplyStatus.PartialUpdate]
    )
    deleted = sorted([f for f, s in statuses.items() if s == FileApplyStatus.Delete])

    outcome = "success"
    lines: List[str] = []

    if errs:
        applied_files = set(file_ops.changes_map.keys())
        applied_any = bool(applied_files)
        applied_created = sorted([f for f in created if f in applied_files])
        applied_updated_full = sorted([f for f in updated_full if f in applied_files])
        applied_updated_partial = sorted(
            [f for f in updated_partial if f in applied_files]
        )
        applied_deleted = sorted([f for f in deleted if f in applied_files])
        failed_files = sorted({e.filename for e in errs if e.filename})  # type: ignore[attr-defined]
        not_applied_failed = sorted([f for f in failed_files if f not in applied_files])

        if not applied_any:
            lines.append("Patch application failed. No changes were applied.")
        else:
            lines.append("Patch application completed with errors. Summary:")
            if applied_created:
                lines.append("Added files (fully applied):")
                for f in applied_created:
                    lines.append(f"* {f}")
            if applied_updated_full:
                lines.append("Fully updated files:")
                for f in applied_updated_full:
                    lines.append(f"* {f}")
            if applied_updated_partial:
                lines.append("Partially updated files (some chunks failed):")
                for f in applied_updated_partial:
                    lines.append(f"* {f}")
            if applied_deleted:
                lines.append("Deleted files (fully applied):")
                for f in applied_deleted:
                    lines.append(f"* {f}")

        targets_for_fix = sorted(set(applied_updated_partial) | set(not_applied_failed))
        if targets_for_fix:
            lines.append(
                "Please regenerate patch chunks for the failed parts in these files:"
            )
            for f in targets_for_fix:
                lines.append(f"* {f}")
            lines.append(
                "If there were other files that were not mentioned in this response, regenerate chunks for them as well. You might want to re-read the source files."
            )

        lines.append("Errors:")
        for e in errs:
            msg = e.msg  # type: ignore[attr-defined]
            hint = e.hint  # type: ignore[attr-defined]
            filename = e.filename  # type: ignore[attr-defined]
            line_no = e.line  # type: ignore[attr-defined]
            loc = ""
            if filename and line_no is not None:
                loc = f"{filename}:{line_no}: "
            elif filename:
                loc = f"{filename}: "
            lines.append(f"* {loc}{msg}")
            if hint:
                lines.append(f"  Hint: {hint}")
        outcome = "fail"
    else:
        lines = ["Applied patch successfully."]
        if created:
            lines.append("Added files:")
            for f in created:
                lines.append(f"* {f}")
        if updated_full:
            lines.append("Fully updated files:")
            for f in updated_full:
                lines.append(f"* {f}")
        if updated_partial:
            lines.append("Partially updated files:")
            for f in updated_partial:
                lines.append(f"* {f}")
        if deleted:
            lines.append("Deleted files:")
            for f in deleted:
                lines.append(f"* {f}")

    summary = "\n".join(lines)
    return summary, outcome, file_ops.changes_map, statuses, errs
