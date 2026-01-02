from __future__ import annotations

import asyncio
import os

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


def test_handler_single_escape_emits_key_event(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()

    def _noop_setup(self) -> None:
        return

    def _noop_teardown(self) -> None:
        return

    monkeypatch.setattr(posix.PosixInputHandler, "_setup_terminal", _noop_setup)
    monkeypatch.setattr(posix.PosixInputHandler, "_teardown_terminal", _noop_teardown)

    events: list[base.InputEvent] = []

    async def main() -> None:
        handler = posix.PosixInputHandler(
            fd=read_fd,
            esc_sequence_timeout=0.1,
            select_idle_timeout=0.1,
        )
        handler.subscribe(events.append)
        task = asyncio.create_task(handler.run())
        os.write(write_fd, b"\x1b")
        await asyncio.sleep(0.3)
        handler.stop()
        await task

    asyncio.run(main())

    esc_events = [e for e in events if isinstance(e, base.KeyEvent) and e.key == "esc"]
    assert esc_events


def test_reader_loop_uses_long_timeout_idle_and_short_with_esc(monkeypatch) -> None:
    handler = posix.PosixInputHandler(fd=0)
    timeouts: list[float] = []

    def fake_select(rlist, wlist, xlist, timeout):
        timeouts.append(timeout)
        handler._running = False
        return [], [], []

    monkeypatch.setattr(posix.select, "select", fake_select)

    handler._running = True
    handler._esc_pending = False
    handler._esc_time = None
    handler._reader_loop()

    assert timeouts
    idle_timeout = timeouts[-1]
    assert idle_timeout > posix.ESC_SEQUENCE_TIMEOUT

    timeouts.clear()

    now = 100.0

    def fake_monotonic() -> float:
        return now

    monkeypatch.setattr(posix.time, "monotonic", fake_monotonic)

    handler._running = True
    handler._esc_pending = True
    handler._esc_time = now
    handler._reader_loop()

    assert timeouts
    esc_timeout = timeouts[-1]
    assert esc_timeout <= posix.ESC_SEQUENCE_TIMEOUT
    assert esc_timeout < idle_timeout
