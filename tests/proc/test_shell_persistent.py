import asyncio
from typing import AsyncIterator, Optional

import pytest

from vocode.proc.shell_persistent import PersistentShellCommand, PersistentShellProcessor


class _DummyHandle:
    """Minimal ProcessHandle-like object for testing PersistentShellCommand."""

    def __init__(
        self,
        stdout_lines: list[str],
        stderr_lines: Optional[list[str]] = None,
        hang_after_first_stderr: bool = False,
    ) -> None:
        self.id = "dummy"
        self.name: Optional[str] = "dummy"
        self._stdout_lines = list(stdout_lines)
        self._stderr_lines = list(stderr_lines or [])
        self._hang_after_first_stderr = hang_after_first_stderr
        self._alive = True
        self._returncode: Optional[int] = None

    @property
    def pid(self) -> Optional[int]:
        return 1234

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def alive(self) -> bool:
        return self._alive

    async def write(self, data: str | bytes) -> None:
        return None

    async def close_stdin(self) -> None:
        return None

    async def iter_stdout(self) -> AsyncIterator[str]:
        for line in self._stdout_lines:
            yield line

    async def iter_stderr(self) -> AsyncIterator[str]:
        if not self._hang_after_first_stderr:
            for line in self._stderr_lines:
                yield line
            return
        # Emit a single line, then hang indefinitely unless cancelled.
        if self._stderr_lines:
            yield self._stderr_lines[0]
        while True:
            await asyncio.sleep(3600)

    async def terminate(self, grace_s: float = 5.0) -> None:
        self._alive = False

    async def kill(self) -> None:
        self._alive = False

    async def wait(self) -> int:
        self._alive = False
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


class _DummyProcessor:
    """Minimal processor facade exposing .handle and on_command_finished."""

    def __init__(self, handle: _DummyHandle) -> None:
        self._handle = handle
        self.finished: list[PersistentShellCommand] = []

    @property
    def handle(self) -> _DummyHandle:
        return self._handle

    def on_command_finished(self, cmd: PersistentShellCommand) -> None:
        self.finished.append(cmd)


@pytest.mark.asyncio
async def test_wait_without_stdout_consumer_buffers_and_detects_marker() -> None:
    marker = "VOCODE_TEST_MARK"
    stdout_lines = ["out1\n", f"{marker}:0\n"]
    handle = _DummyHandle(stdout_lines=stdout_lines)
    processor = _DummyProcessor(handle=handle)
    cmd = PersistentShellCommand(processor=processor, marker=marker, name="test")
    # No stdout consumer: wait() must still complete by detecting the marker.
    rc = await cmd.wait()
    assert rc == 0
    assert processor.finished == [cmd]

    # Later stdout consumer should see the buffered non-marker output.
    collected: list[str] = []
    async for line in cmd.iter_stdout():
        collected.append(line)
    assert collected == ["out1\n"]


@pytest.mark.asyncio
async def test_stderr_streams_and_closes_when_marker_seen() -> None:
    marker = "VOCODE_TEST_MARK_ERR"
    # Command only emits a marker on stdout; stderr emits a line then hangs.
    stdout_lines = [f"{marker}:1\n"]
    handle = _DummyHandle(
        stdout_lines=stdout_lines,
        stderr_lines=["err-before-hang\n"],
        hang_after_first_stderr=True,
    )
    processor = _DummyProcessor(handle=handle)
    cmd = PersistentShellCommand(processor=processor, marker=marker, name="test")

    async def _collect_stderr() -> list[str]:
        lines: list[str] = []
        async for line in cmd.iter_stderr():
            lines.append(line)
        return lines

    stderr_task = asyncio.create_task(_collect_stderr())

    # wait() must detect the marker on stdout and cause stderr streaming to stop.
    rc = await cmd.wait()
    assert rc == 1
    assert processor.finished == [cmd]

    stderr_lines = await asyncio.wait_for(stderr_task, timeout=1.0)
    # The iterator must terminate and should not hang; if any lines were
    # delivered before the marker, they must include the first stderr line.
    assert isinstance(stderr_lines, list)
    if stderr_lines:
        assert "err-before-hang\n" in stderr_lines


class _DummySettings:
    def __init__(self) -> None:
        self.program = "sh"
        self.args: list[str] = []


def _make_processor_for_wrap() -> PersistentShellProcessor:
    proc = object.__new__(PersistentShellProcessor)
    proc._settings = _DummySettings()
    return proc


def test_wrap_command_forces_newline_before_marker() -> None:
    proc = _make_processor_for_wrap()
    marker = "VOCODE_TEST_MARK_WRAP"
    cmd_str = proc._wrap_command_with_marker(
        "printf 'a'; sleep 0.1; printf 'b'", marker
    )
    assert "printf '\\n%s:%s\\n'" in cmd_str
    assert marker in cmd_str


@pytest.mark.asyncio
async def test_last_empty_line_before_marker_is_suppressed() -> None:
    marker = "VOCODE_TEST_MARK_SUPPRESS"
    stdout_lines = ["out1\n", "\n", f"{marker}:0\n"]
    handle = _DummyHandle(stdout_lines=stdout_lines)
    processor = _DummyProcessor(handle=handle)
    cmd = PersistentShellCommand(processor=processor, marker=marker, name="test")

    rc = await cmd.wait()
    assert rc == 0
    assert processor.finished == [cmd]

    collected: list[str] = []
    async for line in cmd.iter_stdout():
        collected.append(line)

    assert collected == ["out1\n"]
