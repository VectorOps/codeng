from __future__ import annotations

from typing import Final

SPINNER_FRAMES_UNICODE: Final[tuple[str, ...]] = (
    " ⠋ ",
    " ⠙ ",
    " ⠹ ",
    " ⠸ ",
    " ⠼ ",
    " ⠴ ",
    " ⠦ ",
    " ⠧ ",
    " ⠇ ",
    " ⠏ ",
)

SPINNER_FRAMES_FALLBACK: Final[tuple[str, ...]] = (" . ", ".. ", "...")
