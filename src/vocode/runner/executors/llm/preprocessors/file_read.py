from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional
from typing import Sequence

from vocode import models as models_mod
from vocode.state import Message
from vocode.runner.executors.llm.preprocessors import base as pre_base


def _validate_relpath(rel: str, project: Any) -> Path | None:
    try:
        p = Path(rel)
        if p.is_absolute():
            return None
        base = project.base_path.resolve()
        full = (project.base_path / p).resolve()
        _ = full.relative_to(base)
        if not full.exists() or not full.is_file():
            return None
        return full
    except Exception:
        return None


def _read_file_text(full: Path) -> str:
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        try:
            return full.read_bytes().decode("utf-8", errors="replace")
        except Exception:
            return ""


@pre_base.PreprocessorFactory.register(
    "file_read",
    description=(
        "Reads files from options.paths (or options.files), optionally prepends "
        "per-file template, and concatenates contents; skips invalid/missing paths."
    ),
)
def _fileread_preprocessor(
    project: Any,
    spec: models_mod.PreprocessorSpec,
    messages: List[Message],
) -> List[Message]:
    opts = spec.options or {}

    paths: list[str] = []
    raw = opts.get("paths")
    if isinstance(raw, str):
        paths = [raw]
    elif isinstance(raw, Sequence):
        paths = [p for p in raw if isinstance(p, str)]
    else:
        raw_files = opts.get("files")
        if isinstance(raw_files, str):
            paths = [raw_files]
        elif isinstance(raw_files, Sequence):
            paths = [p for p in raw_files if isinstance(p, str)]

    if "prepend_template" in opts and opts.get("prepend_template") is None:
        prepend_template: Optional[str] = None
    else:
        prepend_template = opts.get("prepend_template") or "User provided {filename}:\n"

    parts: List[str] = []
    base = project.base_path.resolve()
    for rel in paths:
        full = _validate_relpath(rel, project)
        if not full:
            continue
        try:
            rel_display = str(full.relative_to(base))
        except Exception:
            rel_display = full.name
        if prepend_template is not None:
            try:
                parts.append(
                    prepend_template.format(
                        filename=full.name,
                        path=rel_display,
                    )
                )
            except Exception:
                parts.append(str(prepend_template))
        parts.append(_read_file_text(full))

    inject = "".join(parts)
    if not inject:
        return messages

    target_message: Optional[Message] = None
    if not messages:
        role = (
            models_mod.Role.SYSTEM
            if spec.mode == models_mod.Role.SYSTEM
            else models_mod.Role.USER
        )
        target_message = Message(text="", role=role)
        messages.append(target_message)
    else:
        if spec.mode == models_mod.Role.SYSTEM:
            for msg in messages:
                if msg.role == models_mod.Role.SYSTEM:
                    target_message = msg
                    break
        elif spec.mode == models_mod.Role.USER:
            for msg in reversed(messages):
                if msg.role == models_mod.Role.USER:
                    target_message = msg
                    break

    if target_message:
        base_text = target_message.text or ""
        sep = opts.get("separator", "\n\n")
        if spec.prepend:
            already = f"{inject}{sep}"
        else:
            already = f"{sep}{inject}"
        if already and already in base_text:
            return messages

        if spec.prepend:
            target_message.text = f"{inject}{sep}{base_text}"
        else:
            target_message.text = f"{base_text}{sep}{inject}"

    return messages
