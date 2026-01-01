from __future__ import annotations

import asyncio
import os
import select
import sys
import threading
import time
import typing
from dataclasses import dataclass
import signal

from . import base as input_base


@dataclass(frozen=True)
class KeyMapping:
    name: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    text: str | None = None


def _build_key_sequence_map() -> dict[bytes, KeyMapping]:
    mapping: dict[bytes, KeyMapping] = {}
    mapping[b"\t"] = KeyMapping(name="tab", text="\t")
    mapping[b"\r"] = KeyMapping(name="enter", text="\n")
    mapping[b"\n"] = KeyMapping(name="enter", text="\n")
    mapping[b"\x7f"] = KeyMapping(name="backspace")
    mapping[b"\x08"] = KeyMapping(name="backspace")
    mapping[b"\x1b[A"] = KeyMapping(name="up")
    mapping[b"\x1b[B"] = KeyMapping(name="down")
    mapping[b"\x1b[C"] = KeyMapping(name="right")
    mapping[b"\x1b[D"] = KeyMapping(name="left")
    mapping[b"\x1bOA"] = KeyMapping(name="up")
    mapping[b"\x1bOB"] = KeyMapping(name="down")
    mapping[b"\x1bOC"] = KeyMapping(name="right")
    mapping[b"\x1bOD"] = KeyMapping(name="left")
    mapping[b"\x1b[1;3A"] = KeyMapping(name="up", alt=True)
    mapping[b"\x1b[1;3B"] = KeyMapping(name="down", alt=True)
    mapping[b"\x1b[1;3C"] = KeyMapping(name="right", alt=True)
    mapping[b"\x1b[1;3D"] = KeyMapping(name="left", alt=True)
    mapping[b"\x1b[H"] = KeyMapping(name="home")
    mapping[b"\x1b[F"] = KeyMapping(name="end")
    mapping[b"\x1b[1~"] = KeyMapping(name="home")
    mapping[b"\x1b[4~"] = KeyMapping(name="end")
    mapping[b"\x1b[7~"] = KeyMapping(name="home")
    mapping[b"\x1b[8~"] = KeyMapping(name="end")
    mapping[b"\x1bOH"] = KeyMapping(name="home")
    mapping[b"\x1bOF"] = KeyMapping(name="end")
    mapping[b"\x1b[2~"] = KeyMapping(name="insert")
    mapping[b"\x1b[3~"] = KeyMapping(name="delete")
    mapping[b"\x1b[5~"] = KeyMapping(name="page_up")
    mapping[b"\x1b[6~"] = KeyMapping(name="page_down")
    mapping[b"\x1b[Z"] = KeyMapping(name="tab", shift=True, text="\t")
    mapping[b"\x1bOP"] = KeyMapping(name="f1")
    mapping[b"\x1bOQ"] = KeyMapping(name="f2")
    mapping[b"\x1bOR"] = KeyMapping(name="f3")
    mapping[b"\x1bOS"] = KeyMapping(name="f4")
    mapping[b"\x1b[11~"] = KeyMapping(name="f1")
    mapping[b"\x1b[12~"] = KeyMapping(name="f2")
    mapping[b"\x1b[13~"] = KeyMapping(name="f3")
    mapping[b"\x1b[14~"] = KeyMapping(name="f4")
    mapping[b"\x1b[15~"] = KeyMapping(name="f5")
    mapping[b"\x1b[17~"] = KeyMapping(name="f6")
    mapping[b"\x1b[18~"] = KeyMapping(name="f7")
    mapping[b"\x1b[19~"] = KeyMapping(name="f8")
    mapping[b"\x1b[20~"] = KeyMapping(name="f9")
    mapping[b"\x1b[21~"] = KeyMapping(name="f10")
    mapping[b"\x1b[23~"] = KeyMapping(name="f11")
    mapping[b"\x1b[24~"] = KeyMapping(name="f12")
    for index in range(1, 27):
        if index in (9, 10):
            continue
        code = bytes([index])
        name = chr(ord("a") + index - 1)
        mapping[code] = KeyMapping(name=name, ctrl=True)
    return mapping


_KEY_SEQUENCE_MAP: typing.Final[dict[bytes, KeyMapping]] = _build_key_sequence_map()
_KEY_SEQUENCES: typing.Final[list[bytes]] = sorted(
    _KEY_SEQUENCE_MAP.keys(), key=len, reverse=True
)
_KEY_PREFIXES: typing.Final[set[bytes]] = set()
for _seq in _KEY_SEQUENCE_MAP:
    if len(_seq) <= 1:
        continue
    for _i in range(1, len(_seq)):
        _KEY_PREFIXES.add(_seq[:_i])


ESC_SEQUENCE_TIMEOUT: typing.Final[float] = 1.0
SELECT_IDLE_TIMEOUT: typing.Final[float] = 10.0


class PosixInputDecoder:
    PASTE_START: typing.Final[bytes] = b"\x1b[200~"
    PASTE_END: typing.Final[bytes] = b"\x1b[201~"

    def __init__(self) -> None:
        self._buffer: bytes = b""
        self._paste_mode = False
        self._paste_buffer: bytes = b""

    def feed(self, data: bytes) -> list[input_base.InputEvent]:
        if not data:
            return []
        self._buffer += data
        events: list[input_base.InputEvent] = []
        while self._buffer:
            if self._paste_mode:
                end_index = self._buffer.find(self.PASTE_END)
                if end_index == -1:
                    self._paste_buffer += self._buffer
                    self._buffer = b""
                    break
                self._paste_buffer += self._buffer[:end_index]
                self._buffer = self._buffer[end_index + len(self.PASTE_END) :]
                text = self._paste_buffer.decode(errors="replace")
                self._paste_buffer = b""
                self._paste_mode = False
                events.append(input_base.PasteEvent(text=text))
                continue
            if self._buffer.startswith(self.PASTE_START):
                self._buffer = self._buffer[len(self.PASTE_START) :]
                self._paste_mode = True
                self._paste_buffer = b""
                continue
            mapping = None
            seq_len = 0
            for seq in _KEY_SEQUENCES:
                if self._buffer.startswith(seq):
                    mapping = _KEY_SEQUENCE_MAP[seq]
                    seq_len = len(seq)
                    break
            if mapping is not None:
                self._buffer = self._buffer[seq_len:]
                events.append(
                    input_base.KeyEvent(
                        action="down",
                        key=mapping.name,
                        ctrl=mapping.ctrl,
                        alt=mapping.alt,
                        shift=mapping.shift,
                        text=mapping.text,
                    )
                )
                continue
            if self._buffer in _KEY_PREFIXES:
                break
            if self._buffer[0] == 0x1B and len(self._buffer) >= 2:
                head = self._buffer[:2]
                if head not in _KEY_PREFIXES and head not in _KEY_SEQUENCE_MAP:
                    second_byte = self._buffer[1]
                    self._buffer = self._buffer[2:]
                    base_seq = bytes([second_byte])
                    base_mapping = _KEY_SEQUENCE_MAP.get(base_seq)
                    if base_mapping is not None:
                        events.append(
                            input_base.KeyEvent(
                                action="down",
                                key=base_mapping.name,
                                ctrl=base_mapping.ctrl,
                                alt=True,
                                shift=base_mapping.shift,
                                text=base_mapping.text,
                            )
                        )
                        continue
                    ch = chr(second_byte)
                    if ch.isprintable():
                        shift = ch.isalpha() and ch.isupper()
                        key_name = ch.lower() if shift else ch
                        events.append(
                            input_base.KeyEvent(
                                action="down",
                                key=key_name,
                                alt=True,
                                shift=shift,
                                text=ch,
                            )
                        )
                        continue
            if not self._buffer:
                break
            byte = self._buffer[0]
            self._buffer = self._buffer[1:]
            if 32 <= byte <= 126:
                ch = chr(byte)
                shift = ch.isalpha() and ch.isupper()
                key_name = ch.lower() if shift else ch
                events.append(
                    input_base.KeyEvent(
                        action="down",
                        key=key_name,
                        shift=shift,
                        text=ch,
                    )
                )
                continue
        return events


class PosixInputHandler(input_base.InputHandler):
    def __init__(self, fd: int | None = None) -> None:
        super().__init__()
        if fd is None:
            fd = sys.stdin.fileno()
        self._fd = fd
        self._decoder = PosixInputDecoder()
        self._running = False
        self._orig_termios: typing.Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._resize_installed = False
        self._prev_winch_handler: typing.Any | None = None
        self._esc_pending = False
        self._esc_time: float | None = None

    async def run(self) -> None:
        self.start()
        stop_event = self._stop_event
        if stop_event is None:
            return
        try:
            await stop_event.wait()
        finally:
            self.stop()

    def start(self) -> None:
        if sys.platform == "win32":
            raise RuntimeError("PosixInputHandler is not supported on Windows")
        if self._running:
            return
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._stop_event = asyncio.Event()
        self._setup_terminal()
        self._install_winch_handler()
        self._running = True
        thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._signal_stop()
        thread = self._thread
        if thread is not None:
            thread.join()
            self._thread = None
        self._remove_winch_handler()
        self._teardown_terminal()
        self._loop = None
        self._stop_event = None

    def _setup_terminal(self) -> None:
        import termios
        import tty

        self._orig_termios = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def _teardown_terminal(self) -> None:
        if self._orig_termios is None:
            return
        import termios

        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._orig_termios)
        self._orig_termios = None

    def _install_winch_handler(self) -> None:
        if not hasattr(signal, "SIGWINCH"):
            return
        if self._resize_installed:
            return
        self._prev_winch_handler = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, self._handle_winch)
        self._resize_installed = True

    def _remove_winch_handler(self) -> None:
        if not self._resize_installed:
            return
        if not hasattr(signal, "SIGWINCH"):
            self._resize_installed = False
            self._prev_winch_handler = None
            return
        prev = self._prev_winch_handler
        if prev is not None:
            signal.signal(signal.SIGWINCH, prev)
        self._resize_installed = False
        self._prev_winch_handler = None

    def _handle_winch(self, signum, frame) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._emit_resize_event)

    def _emit_resize_event(self) -> None:
        try:
            size = os.get_terminal_size(self._fd)
        except OSError:
            return
        event = input_base.ResizeEvent(width=size.columns, height=size.lines)
        self.publish(event)

    def _reader_loop(self) -> None:
        try:
            while self._running:
                timeout = SELECT_IDLE_TIMEOUT
                if self._esc_pending and self._esc_time is not None:
                    remaining = ESC_SEQUENCE_TIMEOUT - (
                        time.monotonic() - self._esc_time
                    )
                    if remaining <= 0:
                        loop = self._loop
                        if loop is not None:
                            event = input_base.KeyEvent(
                                action="down",
                                key="esc",
                            )
                            loop.call_soon_threadsafe(self.publish, event)
                        self._esc_pending = False
                        self._esc_time = None
                        continue

                    if remaining < timeout:
                        timeout = max(0.0, remaining)

                rlist, _, _ = select.select([self._fd], [], [], timeout)
                if not rlist:
                    continue

                try:
                    data = os.read(self._fd, 1024)
                except OSError:
                    break

                if not data:
                    self._running = False
                    break

                if self._esc_pending and self._esc_time is not None:
                    data = b"\x1b" + data
                    self._esc_pending = False
                    self._esc_time = None
                elif data == b"\x1b":
                    self._esc_pending = True
                    self._esc_time = time.monotonic()
                    continue

                events = self._decoder.feed(data)
                if not events:
                    continue

                loop = self._loop
                if loop is None:
                    continue

                loop.call_soon_threadsafe(self._dispatch_events, events)
        finally:
            self._signal_stop()

    def _dispatch_events(self, events: list[input_base.InputEvent]) -> None:
        for event in events:
            self.publish(event)

    def _signal_stop(self) -> None:
        loop = self._loop
        stop_event = self._stop_event
        if loop is None or stop_event is None:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
