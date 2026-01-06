from __future__ import annotations

from typing import Any, Dict, List, Optional

from vocode import models as models_mod
from vocode.patch import get_supported_formats, get_system_instruction
from vocode.state import Message

from vocode.runner.executors.llm.preprocessors import base as pre_base


@pre_base.PreprocessorFactory.register(
    "diff",
    description=(
        "Injects system instructions for diff patches. Options: "
        f"{{'format': one of {', '.join(sorted(get_supported_formats()))}}}"
    ),
)
def _diff_preprocessor(
    project: Any,
    spec: models_mod.PreprocessorSpec,
    messages: List[Message],
) -> List[Message]:
    fmt: Optional[str] = (spec.options or {}).get("format", "v4a")
    if isinstance(fmt, str):
        fmt = fmt.lower().strip()
    else:
        fmt = "v4a"

    if fmt not in get_supported_formats():
        return messages

    instruction = get_system_instruction(fmt)

    suffix: str = (spec.options or {}).get("suffix", "")
    target_message: Optional[Message] = None

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
        existing = target_message.text or ""
        if instruction in existing:
            return messages

        if spec.prepend:
            target_message.text = f"{instruction}{suffix}{existing}"
        else:
            target_message.text = f"{existing}{suffix}{instruction}"

    return messages