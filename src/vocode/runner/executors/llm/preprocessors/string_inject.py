from __future__ import annotations

from typing import Any, List

from vocode import models as models_mod
from vocode.state import Message
from vocode.runner.executors.llm.preprocessors import base as pre_base


@pre_base.PreprocessorFactory.register(
    "string_inject",
    description=(
        "Injects a literal string from options.text into the system or user "
        "message, chosen by spec.mode; supports prepend/append and a configurable separator."
    ),
)
def _string_inject_preprocessor(
    project: Any,
    spec: models_mod.PreprocessorSpec,
    messages: List[Message],
) -> List[Message]:
    """
    Injects a literal string from options['text'] into either the system or user
    message, based on spec.mode.
    """
    opts = spec.options or {}
    raw = opts.get("text")

    if not isinstance(raw, str):
        return messages

    inject = raw.strip()
    if not inject:
        return messages

    target_message: Message | None = None

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

    if not target_message:
        return messages

    base_text = target_message.text or ""
    if inject in base_text:
        return messages

    separator = opts.get("separator", "\n\n")

    if spec.prepend:
        if base_text:
            target_message.text = f"{inject}{separator}{base_text}"
        else:
            target_message.text = inject
    else:
        if base_text:
            target_message.text = f"{base_text}{separator}{inject}"
        else:
            target_message.text = inject

    return messages