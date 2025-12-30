from __future__ import annotations

from vocode.tui.lib.input import base
from vocode.tui.lib.input import posix


def test_decoder_simple_character() -> None:
    decoder = posix.PosixInputDecoder()
    events = decoder.feed(b"a")
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, base.KeyEvent)
    assert event.action == "down"
    assert event.key == "a"
    assert event.text == "a"
    assert not event.ctrl
    assert not event.alt
    assert not event.shift


def test_decoder_enter_and_backspace() -> None:
    decoder = posix.PosixInputDecoder()
    events = decoder.feed(b"\n\x7f")
    assert len(events) == 2
    enter_event = events[0]
    backspace_event = events[1]
    assert isinstance(enter_event, base.KeyEvent)
    assert enter_event.key == "enter"
    assert enter_event.text == "\n"
    assert isinstance(backspace_event, base.KeyEvent)
    assert backspace_event.key == "backspace"


def test_decoder_arrow_keys() -> None:
    decoder = posix.PosixInputDecoder()
    events = decoder.feed(b"\x1b[A\x1b[B\x1b[C\x1b[D")
    keys = [e.key for e in events if isinstance(e, base.KeyEvent)]
    assert keys == ["up", "down", "right", "left"]


def test_decoder_alt_arrow_keys() -> None:
    decoder = posix.PosixInputDecoder()
    data = b"\x1b[1;3A\x1b[1;3B\x1b[1;3C\x1b[1;3D"
    events = decoder.feed(data)
    keys = [(e.key, e.alt) for e in events if isinstance(e, base.KeyEvent)]
    assert keys == [
        ("up", True),
        ("down", True),
        ("right", True),
        ("left", True),
    ]


def test_decoder_bracketed_paste() -> None:
    decoder = posix.PosixInputDecoder()
    data = b"\x1b[200~hello world\x1b[201~"
    events = decoder.feed(data)
    assert len(events) == 1
    paste_event = events[0]
    assert isinstance(paste_event, base.PasteEvent)
    assert paste_event.text == "hello world"
