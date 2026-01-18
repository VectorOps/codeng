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
from vocode.logger import logger


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

    def _append_char_key(
        self,
        events: list[input_base.InputEvent],
        ch: str,
        *,
        alt: bool = False,
        ctrl: bool = False,
        shift: bool = False,
    ) -> None:
        # Printable-key events normalize uppercase letters to lowercase key names.
        alpha_shift = ch.isalpha() and ch.isupper()
        key_name = ch.lower() if alpha_shift else ch
        events.append(
            input_base.KeyEvent(
                action="down",
                key=key_name,
                ctrl=ctrl,
                alt=alt,
                shift=shift or alpha_shift,
                text=ch,
            )
        )

    def _try_consume_paste_mode(
        self, events: list[input_base.InputEvent]
    ) -> tuple[bool, bool]:
        # In bracketed paste mode, buffer until the end marker and emit one PasteEvent.
        if not self._paste_mode:
            return False, False

        end_index = self._buffer.find(self.PASTE_END)
        if end_index == -1:
            self._paste_buffer += self._buffer
            self._buffer = b""
            return True, True

        self._paste_buffer += self._buffer[:end_index]
        self._buffer = self._buffer[end_index + len(self.PASTE_END) :]
        text = self._paste_buffer.decode(errors="replace")
        self._paste_buffer = b""
        self._paste_mode = False
        events.append(input_base.PasteEvent(text=text))
        return True, False

    def _try_consume_csi_u(
        self, events: list[input_base.InputEvent]
    ) -> tuple[bool, bool]:
        # Parse CSI-u (kitty keyboard protocol): ESC [ <codepoint> ; <mods> u
        if not self._buffer.startswith(b"\x1b["):
            return False, False

        idx = 2
        if idx >= len(self._buffer):
            return False, True

        if not (48 <= self._buffer[idx] <= 57):
            return False, False

        start_code = idx
        while idx < len(self._buffer) and 48 <= self._buffer[idx] <= 57:
            idx += 1
        if idx >= len(self._buffer):
            return False, True
        code_str = self._buffer[start_code:idx].decode(errors="replace")

        mod_str = None
        if self._buffer[idx : idx + 1] == b";":
            idx += 1
            start_mod = idx
            while idx < len(self._buffer) and 48 <= self._buffer[idx] <= 57:
                idx += 1
            if idx >= len(self._buffer):
                return False, True
            mod_str = self._buffer[start_mod:idx].decode(errors="replace")

        if self._buffer[idx : idx + 1] != b"u":
            return False, False

        idx += 1
        try:
            codepoint = int(code_str)
        except ValueError:
            codepoint = -1
        mods = 1
        if mod_str is not None:
            try:
                mods = int(mod_str)
            except ValueError:
                mods = 1

        if not (32 <= codepoint <= 126):
            return False, False

        ch = chr(codepoint)
        ctrl = mods in (5, 6, 7, 8)
        alt = mods in (3, 4, 7, 8)
        shift = mods in (2, 4, 6, 8)
        self._append_char_key(events, ch, ctrl=ctrl, alt=alt, shift=shift)
        self._buffer = self._buffer[idx:]
        return True, False

    def _try_consume_paste_start(self) -> bool:
        # Start bracketed paste mode when we see the CSI 200~ marker.
        if not self._buffer.startswith(self.PASTE_START):
            return False

        self._buffer = self._buffer[len(self.PASTE_START) :]
        self._paste_mode = True
        self._paste_buffer = b""
        return True

    def _try_consume_mapped_sequence(self, events: list[input_base.InputEvent]) -> bool:
        # Match fixed escape sequences (arrows, function keys, etc).
        for seq in _KEY_SEQUENCES:
            if not self._buffer.startswith(seq):
                continue
            mapping = _KEY_SEQUENCE_MAP[seq]
            self._buffer = self._buffer[len(seq) :]
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
            return True
        return False

    def _try_consume_alt_modified(self, events: list[input_base.InputEvent]) -> bool:
        # Treat ESC + <byte> as Alt-modified input when it is not a known sequence.
        if not self._buffer or self._buffer[0] != 0x1B or len(self._buffer) < 2:
            return False

        head = self._buffer[:2]
        if head in _KEY_PREFIXES or head in _KEY_SEQUENCE_MAP:
            return False

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
            return True

        ch = chr(second_byte)
        if ch.isprintable():
            self._append_char_key(events, ch, alt=True)
        return True

    def _consume_one_byte(self, events: list[input_base.InputEvent]) -> None:
        # Fallback: consume one byte; emit a KeyEvent only for printable ASCII.
        byte = self._buffer[0]
        self._buffer = self._buffer[1:]
        if 32 <= byte <= 126:
            self._append_char_key(events, chr(byte))

    def feed(self, data: bytes) -> list[input_base.InputEvent]:
        if not data:
            return []
        self._buffer += data
        events: list[input_base.InputEvent] = []

        # Parse as many events as possible from the accumulated byte buffer.
        while self._buffer:
            consumed, need_more = self._try_consume_paste_mode(events)
            if need_more:
                break
            if consumed:
                continue

            consumed, need_more = self._try_consume_csi_u(events)
            if need_more:
                break
            if consumed:
                continue

            if self._try_consume_paste_start():
                continue

            if self._try_consume_mapped_sequence(events):
                continue

            # If we have an escape-sequence prefix, wait for more bytes.
            if self._buffer in _KEY_PREFIXES:
                break

            if self._try_consume_alt_modified(events):
                continue

            self._consume_one_byte(events)
        return events


class PosixInputHandler(input_base.InputHandler):
    def __init__(
        self,
        fd: int | None = None,
        esc_sequence_timeout: float = ESC_SEQUENCE_TIMEOUT,
        select_idle_timeout: float = SELECT_IDLE_TIMEOUT,
    ) -> None:
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
        self._esc_sequence_timeout = esc_sequence_timeout
        self._select_idle_timeout = select_idle_timeout
        self._sigint_installed = False
        self._prev_sigint_handler: typing.Any | None = None

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
        self._install_sigint_handler()
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
        self._remove_sigint_handler()
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

    def _install_sigint_handler(self) -> None:
        if not hasattr(signal, "SIGINT"):
            return
        if self._sigint_installed:
            return
        try:
            prev = signal.getsignal(signal.SIGINT)
            self._prev_sigint_handler = prev

            def _handle_sigint(signum, frame) -> None:
                _ = signum
                _ = frame
                loop = self._loop
                if loop is None or loop.is_closed():
                    return
                event = input_base.KeyEvent(
                    action="down",
                    key="c",
                    ctrl=True,
                )
                loop.call_soon_threadsafe(self.publish, event)

            signal.signal(signal.SIGINT, _handle_sigint)
            self._sigint_installed = True
        except ValueError:
            self._prev_sigint_handler = None

    def _remove_sigint_handler(self) -> None:
        if not self._sigint_installed:
            return
        if not hasattr(signal, "SIGINT"):
            self._sigint_installed = False
            self._prev_sigint_handler = None
            return
        prev = self._prev_sigint_handler
        if prev is not None:
            try:
                signal.signal(signal.SIGINT, prev)
            except ValueError:
                pass
        self._sigint_installed = False
        self._prev_sigint_handler = None

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
                timeout = self._select_idle_timeout
                if self._esc_pending and self._esc_time is not None:
                    remaining = self._esc_sequence_timeout - (
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
