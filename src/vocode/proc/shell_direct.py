from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional

from vocode import settings as vsettings

from .manager import ProcessManager
from .base import ProcessHandle
from .shell_base import ShellCommandHandle, ShellProcessor


class DirectShellCommand(ShellCommandHandle):
    """
    Simple adapter that forwards all operations to the underlying ProcessHandle.
    """

    def __init__(self, handle: ProcessHandle, processor: "DirectShellProcessor") -> None:
        self._handle = handle
        self._processor = processor
        self.id = handle.id
        self.name = handle.name

    @property
    def pid(self) -> Optional[int]:
        return self._handle.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._handle.returncode

    def alive(self) -> bool:
        return self._handle.alive()

    async def write(self, data: str | bytes) -> None:
        await self._handle.write(data)

    async def close_stdin(self) -> None:
        await self._handle.close_stdin()

    async def iter_stdout(self) -> AsyncIterator[str]:
        async for line in self._handle.iter_stdout():
            yield line

    async def iter_stderr(self) -> AsyncIterator[str]:
        async for line in self._handle.iter_stderr():
            yield line

    async def terminate(self, grace_s: float = 5.0) -> None:
        await self._handle.terminate(grace_s=grace_s)

    async def kill(self) -> None:
        await self._handle.kill()

    async def wait(self) -> int:
        try:
            return await self._handle.wait()
        finally:
            self._processor.forget(self._handle.id)


class DirectShellProcessor(ShellProcessor):
    """
    Direct mode: each command runs in its own subprocess via ProcessManager.
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
        self._handles: dict[str, ProcessHandle] = {}

    async def start(self) -> None:
        # No long-lived process to initialize in direct mode.
        return

    async def stop(self) -> None:
        handles = list(self._handles.values())
        self._handles.clear()
        if not handles:
            return

        terminates = [h.terminate(grace_s=1.0) for h in handles if h.alive()]
        if terminates:
            await asyncio.gather(*terminates, return_exceptions=True)

        kills = [h.kill() for h in handles if h.alive()]
        if kills:
            await asyncio.gather(*kills, return_exceptions=True)

        waits = [h.wait() for h in handles]
        if waits:
            await asyncio.gather(*waits, return_exceptions=True)

    async def run(self, command: str) -> ShellCommandHandle:
        handle = await self._pm.spawn(
            command=command,
            cwd=self._default_cwd,
            env_overlay=self._env_overlay or None,
            shell=True,
        )
        self._handles[handle.id] = handle
        return DirectShellCommand(handle, self)

    def forget(self, handle_id: str) -> None:
        self._handles.pop(handle_id, None)