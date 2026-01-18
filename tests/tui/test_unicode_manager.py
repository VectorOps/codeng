from __future__ import annotations

from vocode.tui.lib import unicode as tui_unicode
from vocode.settings.models import TUIOptions


def test_unicode_manager_glyph_unicode_default() -> None:
    tui = TUIOptions(unicode=True, ascii_fallback=False)
    manager = tui_unicode.UnicodeManager(tui)

    assert manager.glyph(":question:") == "\u2753"
    assert manager.glyph("black_question_mark_ornament") == "\u2753\ufe0e"


def test_unicode_manager_glyph_ascii_fallback() -> None:
    tui = TUIOptions(unicode=True, ascii_fallback=True)
    manager = tui_unicode.UnicodeManager(tui)

    assert manager.glyph(":question:") == "?"


def test_unicode_manager_spinner_frames_ascii_fallback() -> None:
    tui = TUIOptions(unicode=True, ascii_fallback=True)
    manager = tui_unicode.UnicodeManager(tui)

    frames = manager.spinner_frames(tui_unicode.SpinnerVariant.BRAILLE)
    assert frames == (" . ", ".. ", "...")


def test_unicode_manager_spinner_frames_unicode_default() -> None:
    tui = TUIOptions(unicode=True, ascii_fallback=False)
    manager = tui_unicode.UnicodeManager(tui)

    frames = manager.spinner_frames(tui_unicode.SpinnerVariant.BRAILLE)
    assert frames[0] == " ⠋ "
