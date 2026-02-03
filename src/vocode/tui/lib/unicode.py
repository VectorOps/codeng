from __future__ import annotations

import enum
import typing

from typing import Final
from vocode.settings.models import TUIOptions


class SpinnerVariant(str, enum.Enum):
    BRAILLE = "braille"
    DOTS = "dots"


class UnicodeManager:
    _EMOJI_UNICODE: Final[dict[str, str]] = {
        ":question:": "\u2753",  # ❓
        ":hourglass_not_done:": "\u23f3",  # ⏳
        ":x:": "\u274c",  # ❌
        ":white_check_mark:": "\u2705",  # ✅
        "black_question_mark_ornament": "\u2753\ufe0e",
        "hourglass_with_flowing_sand": "\u23f3\ufe0e",
        "heavy_multiplication_x": "\u2716\ufe0e",
        "heavy_check_mark": "\u2714\ufe0e",
        ":circle:": "\u25cf",
    }

    _EMOJI_ASCII: Final[dict[str, str]] = {
        ":question:": "?",
        ":hourglass_not_done:": "...",
        ":x:": "x",
        ":white_check_mark:": "ok",
        "black_question_mark_ornament": "[?]",
        "hourglass_with_flowing_sand": "...",
        "heavy_multiplication_x": "[x]",
        "heavy_check_mark": "[+]",
        ":circle:": "*",
    }

    _SPINNER_UNICODE: Final[dict[SpinnerVariant, tuple[str, ...]]] = {
        SpinnerVariant.BRAILLE: (
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
        ),
        SpinnerVariant.DOTS: (" . ", ".. ", "..."),
    }

    _SPINNER_ASCII: Final[dict[SpinnerVariant, tuple[str, ...]]] = {
        SpinnerVariant.BRAILLE: (" . ", ".. ", "..."),
        SpinnerVariant.DOTS: (" . ", ".. ", "..."),
    }

    def __init__(self, settings: TUIOptions | None = None) -> None:
        unicode_enabled = True if settings is None else bool(settings.unicode)
        ascii_fallback = False if settings is None else bool(settings.ascii_fallback)
        self._unicode_enabled = unicode_enabled
        self._ascii_fallback = (not unicode_enabled) or ascii_fallback

    @property
    def ascii_fallback(self) -> bool:
        return self._ascii_fallback

    def glyph(self, name: str, *, ascii: bool | None = None) -> str:
        use_ascii = self._ascii_fallback if ascii is None else ascii
        if use_ascii:
            mapped = self._EMOJI_ASCII.get(name)
            if mapped is not None:
                return mapped
            return name if name.isascii() else "?"
        mapped = self._EMOJI_UNICODE.get(name)
        if mapped is not None:
            return mapped
        return name

    def spinner_frames(
        self,
        variant: SpinnerVariant | None = None,
        *,
        ascii: bool | None = None,
    ) -> tuple[str, ...]:
        use_ascii = self._ascii_fallback if ascii is None else ascii
        if variant is None:
            variant = SpinnerVariant.DOTS if use_ascii else SpinnerVariant.BRAILLE
        if use_ascii:
            return self._SPINNER_ASCII[variant]
        return self._SPINNER_UNICODE[variant]

    def spinner_frame(
        self,
        index: int,
        variant: SpinnerVariant | None = None,
        *,
        ascii: bool | None = None,
    ) -> str:
        frames = self.spinner_frames(variant, ascii=ascii)
        if not frames:
            return ""
        return frames[index % len(frames)]
