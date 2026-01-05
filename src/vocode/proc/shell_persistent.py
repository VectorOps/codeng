from __future__ import annotations

import asyncio
import contextlib
import shlex
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from vocode import settings as vsettings

from .manager import ProcessManager
from .base import ProcessHandle
from .shell_base import ShellCommandHandle, ShellProcessor


class PersistentShellCommand(ShellCommandHandle):
    """
    Represents a single command executed inside the long-lived shell.

    - iter_stdout streams lines until the marker is seen (marker is not yielded).
    - wait() waits until the marker is seen and returns the parsed exit code.
    """

    def __init__(
        self,
        *,
        processor: "PersistentShellProcessor",
        marker: str,
        name: Optional[str],
    ) -> None:
        self._processor = processor
        self._marker = marker
        self._returncode: Optional[int] = None
        self._done = asyncio.Event()
        self._stdout_consumed = False
        # Background stdout pump and buffers. The pump always reads stdout so
        # that we can detect the marker even when nobody calls iter_stdout().
        self._stdout_task: Optional[asyncio.Task[None]] = None
        self._stdout_buffer: list[str] = []
        self._stdout_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._stdout_lock = asyncio.Lock()
        # Background stderr pump and queue. Started lazily by iter_stderr().
        self._stderr_consumed = False
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._stderr_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._stderr_lock = asyncio.Lock()
        self.id = str(uuid.uuid4())
        self.name = name

    @property
    def pid(self) -> Optional[int]:
        handle = self._processor.handle
        return handle.pid if handle is not None else None

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def alive(self) -> bool:
        handle = self._processor.handle
        return (handle is not None and handle.alive()) and not self._done.is_set()

    async def write(self, data: str | bytes) -> None:
        handle = self._processor.handle
        if handle is None:
            return
        await handle.write(data)

    async def close_stdin(self) -> None:
        handle = self._processor.handle
        if handle is None:
            return
        await handle.close_stdin()

    def _stop_stderr_streaming(self) -> None:
        """Cancel stderr pump (if running) and signal end-of-stream."""
        stderr_task = self._stderr_task
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
        with contextlib.suppress(asyncio.QueueFull):
            self._stderr_queue.put_nowait(None)

    async def _ensure_stdout_pump(self) -> None:
        # Start a single background task that consumes the persistent shell's
        # stdout, detects the marker, and either buffers or queues lines.
        async with self._stdout_lock:
            if self._stdout_task is not None:
                return

            handle = self._processor.handle
            if handle is None:
                # Shell already gone; mark as failed and signal end-of-stream.
                if not self._done.is_set():
                    self._returncode = (
                        self._returncode if self._returncode is not None else 1
                    )
                    self._done.set()
                    self._processor.on_command_finished(self)
                await self._stdout_queue.put(None)
                return

            stdout_iter = handle.iter_stdout()

            async def _pump() -> None:
                try:
                    async for line in stdout_iter:
                        text = line.rstrip("\r\n")
                        if text.startswith(self._marker):
                            suffix = text[len(self._marker) :]
                            if suffix.startswith(":"):
                                with contextlib.suppress(ValueError):
                                    self._returncode = int(suffix[1:])
                            if self._returncode is None:
                                self._returncode = 0
                            self._done.set()
                            self._processor.on_command_finished(self)
                            # Stop stderr streaming when the command ends.
                            self._stop_stderr_streaming()
                            break

                        # If nobody is consuming yet, keep lines in a buffer.
                        if not self._stdout_consumed:
                            self._stdout_buffer.append(line)
                        else:
                            await self._stdout_queue.put(line)
                finally:
                    with contextlib.suppress(Exception):
                        await stdout_iter.aclose()
                    if not self._done.is_set():
                        # Shell exited without emitting the marker; treat as unknown non-zero.
                        self._returncode = (
                            self._returncode if self._returncode is not None else 1
                        )
                        self._done.set()
                        self._processor.on_command_finished(self)
                        self._stop_stderr_streaming()
                    # Always signal end-of-stream to any stdout consumer.
                    await self._stdout_queue.put(None)

            self._stdout_task = asyncio.create_task(_pump())

    async def _ensure_stderr_pump(self) -> None:
        # Start a single background task that consumes the persistent shell's
        # stderr and forwards it to a per-command queue. The task is cancelled
        # when the stdout marker is detected.
        async with self._stderr_lock:
            if self._stderr_task is not None:
                return

            handle = self._processor.handle
            if handle is None:
                await self._stderr_queue.put(None)
                return

            stderr_iter = handle.iter_stderr()

            async def _pump_err() -> None:
                try:
                    async for line in stderr_iter:
                        await self._stderr_queue.put(line)
                finally:
                    with contextlib.suppress(Exception):
                        await stderr_iter.aclose()
                    await self._stderr_queue.put(None)

            self._stderr_task = asyncio.create_task(_pump_err())

    async def iter_stdout(self) -> AsyncIterator[str]:
        # Expose a single consumer view over the background pump. If nobody
        # ever calls iter_stdout(), wait() still completes because the pump
        # runs independently and sees the marker.
        if self._stdout_consumed:
            return
        self._stdout_consumed = True

        await self._ensure_stdout_pump()

        # First flush any buffered lines accumulated before the consumer attached.
        for line in self._stdout_buffer:
            yield line
        self._stdout_buffer.clear()

        # Then stream from the queue until the sentinel is seen.
        while True:
            item = await self._stdout_queue.get()
            if item is None:
                break
            yield item

    async def iter_stderr(self) -> AsyncIterator[str]:
        # Stream stderr lines for this command. The background stderr pump is
        # cancelled when the stdout marker is detected, at which point a
        # sentinel is pushed and this iterator terminates.
        if self._stderr_consumed:
            return
        self._stderr_consumed = True
        # If the command is already finished, do not start a new stderr pump.
        # ExecExecutor/ExecTool start stderr consumption before waiting, so
        # this mainly guards late callers from hanging.
        if self._done.is_set():
            return

        await self._ensure_stderr_pump()

        while True:
            item = await self._stderr_queue.get()
            if item is None:
                break
            yield item

    async def terminate(self, grace_s: float = 5.0) -> None:
        handle = self._processor.handle
        if handle is None:
            return
        await handle.terminate(grace_s=grace_s)
        if not self._done.is_set():
            self._returncode = self._returncode if self._returncode is not None else 1
            self._done.set()
            self._processor.on_command_finished(self)

    async def kill(self) -> None:
        handle = self._processor.handle
        if handle is None:
            return
        await handle.kill()
        if not self._done.is_set():
            self._returncode = self._returncode if self._returncode is not None else 1
            self._done.set()
            self._processor.on_command_finished(self)

    async def wait(self) -> int:
        # Ensure stdout is being consumed so that the marker is detected even
        # when callers never attach to iter_stdout().
        await self._ensure_stdout_pump()
        await self._done.wait()
        assert self._returncode is not None
        return self._returncode


class PersistentShellProcessor(ShellProcessor):
    """
    Shell mode: maintain a long-lived shell process and run commands within it.

    Commands are wrapped with a marker line "<marker>:<rc>" that is consumed
    internally and not yielded to callers.
    """

    def __init__(
        self,
        *,
        process_manager: ProcessManager,
        settings: vsettings.ShellSettings,
        default_cwd: Optional[Path],
        env_overlay: dict[str, str],
    ) -> None:
        self._pm = process_manager
        self._settings = settings
        self._default_cwd = default_cwd
        self._env_overlay = env_overlay
        self._handle: Optional[ProcessHandle] = None
        self._active_cmd: Optional[PersistentShellCommand] = None
        self._lock = asyncio.Lock()

    @property
    def handle(self) -> Optional[ProcessHandle]:
        return self._handle

    async def start(self) -> None:
        async with self._lock:
            await self._ensure_started()

    async def stop(self) -> None:
        async with self._lock:
            handle = self._handle
            self._handle = None
            self._active_cmd = None

        if handle is None:
            return

        try:
            await handle.terminate(grace_s=1.0)
        except Exception:
            with contextlib.suppress(Exception):
                await handle.kill()
        finally:
            with contextlib.suppress(Exception):
                await handle.wait()

    async def run(self, command: str) -> ShellCommandHandle:
        async with self._lock:
            await self._ensure_started()
            if self._active_cmd is not None and self._active_cmd.alive():
                raise RuntimeError("Another shell command is already running")

            assert self._handle is not None
            marker = f"VOCODE_MARK_{uuid.uuid4().hex}"
            wrapped = self._wrap_command_with_marker(command, marker)
            cmd = PersistentShellCommand(
                processor=self,
                marker=marker,
                name="shell",
            )
            self._active_cmd = cmd
            await self._handle.write(wrapped)
            return cmd

    async def _ensure_started(self) -> None:
        if self._handle is not None and self._handle.alive():
            return
        start_cmd = self._start_command()
        self._handle = await self._pm.spawn(
            command=start_cmd,
            name="shell",
            cwd=self._default_cwd,
            env_overlay=self._env_overlay or None,
        )

    def _start_command(self) -> str:
        parts = [self._settings.program, *self._settings.args]
        return " ".join(shlex.quote(p) for p in parts)

    def _wrap_command_with_marker(self, command: str, marker: str) -> str:
        # Execute the user command in a fresh subshell to insulate parsing
        # errors, capture its exit code, and always print a single-line marker
        # with the exit code appended.
        # Build inner invocation: <program> <args...> -c '<command>'
        tokens: list[str] = [
            self._settings.program,
            *self._settings.args,
            "-c",
            command,
        ]
        inner = " ".join(shlex.quote(t) for t in tokens)
        # Initialize rc to a fallback, run the inner, save rc, then print marker:rc
        # Emit a single marker line as "<marker>:<rc>"
        return (
            "rc=127; "
            f"{{ {inner}; rc=$?; }}; "
            "echo "
            + shlex.quote(marker)
            + ':"$rc"\n'
        )

    def on_command_finished(self, cmd: PersistentShellCommand) -> None:
        if self._active_cmd is cmd:
            self._active_cmd = None