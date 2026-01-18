from __future__ import annotations

import dataclasses
import typing

from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.input import base as input_base


@dataclasses.dataclass(frozen=True)
class Hotkey:
    name: str
    category: str
    mapping: tui_input_component.KeyBinding
    handler: typing.Callable[[input_base.KeyEvent], bool]


def format_keybinding(binding: tui_input_component.KeyBinding) -> str:
    parts: list[str] = []
    if binding.ctrl:
        parts.append("Ctrl")
    if binding.alt:
        parts.append("Alt")
    if binding.shift:
        parts.append("Shift")

    key = binding.key
    if len(key) == 1 and key.isalpha():
        key = key.upper() if binding.shift else key.lower()

    parts.append(key)
    return "+".join(parts)
