from __future__ import annotations

from pathlib import Path
from typing import Optional, Type, AsyncIterator
import asyncio
import contextlib

from vocode import settings as vsettings

from .manager import ProcessManager
from .shell_base import ShellCommandHandle, ShellProcessor
from .shell_direct import DirectShellProcessor
from .shell_persistent import PersistentShellProcessor


PROCESSOR_FACTORY: dict[vsettings.ShellMode, Type[ShellProcessor]] = {
    vsettings.ShellMode.direct: DirectShellProcessor,
    vsettings.ShellMode.shell: PersistentShellProcessor,
}


class ManagedShellCommand(ShellCommandHandle):
    """
    Wraps a ShellCommandHandle and enforces a per-command timeout on wait().

    On timeout:
      - Terminates and kills the underlying command.
      - Raises asyncio.TimeoutError to callers of wait().
    """

    def __init__(
        self,
        inner: ShellCommandHandle,
        timeout: Optional[float],
    ) -> None:
        self._inner = inner
        self._timeout = timeout
        self._wait_task: Optional[asyncio.Task[int]] = None
        self.id = inner.id
        self.name = inner.name

    @property
    def pid(self) -> Optional[int]:
        return self._inner.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._inner.returncode

    def alive(self) -> bool:
        return self._inner.alive()

    async def write(self, data: str | bytes) -> None:
        await self._inner.write(data)

    async def close_stdin(self) -> None:
        await self._inner.close_stdin()

    async def iter_stdout(self) -> AsyncIterator[str]:
        async for line in self._inner.iter_stdout():
            yield line

    async def iter_stderr(self) -> AsyncIterator[str]:
        async for line in self._inner.iter_stderr():
            yield line

    async def terminate(self, grace_s: float = 5.0) -> None:
        await self._inner.terminate(grace_s=grace_s)

    async def kill(self) -> None:
        await self._inner.kill()

    async def wait(self) -> int:
        if self._wait_task is None:
            self._wait_task = asyncio.create_task(self._run_wait())
        return await self._wait_task

    async def _run_wait(self) -> int:
        if self._timeout is not None and self._timeout > 0:
            try:
                return await asyncio.wait_for(
                    self._inner.wait(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await self._inner.terminate(grace_s=1.0)
                with contextlib.suppress(Exception):
                    if self._inner.alive():
                        await self._inner.kill()
                # Propagate timeout to callers.
                raise
        return await self._inner.wait()


class ShellManager:
    """
    High-level manager for running shell commands in either:
      - direct mode: each command is its own subprocess
      - shell mode: commands run via a long-lived shell process with wrapped markers
    """

    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        settings: Optional[vsettings.ShellSettings] = None,
        default_cwd: Optional[Path] = None,
        env_overlay: Optional[dict[str, str]] = None,
    ) -> None:
        self._pm = process_manager
        self._settings = settings or vsettings.ShellSettings()
        self._mode = self._settings.mode
        self._default_cwd = default_cwd
        self._env_overlay: dict[str, str] = dict(env_overlay or {})
        self._processor: Optional[ShellProcessor] = None
        # Serialize shell commands: at most one active command at a time.
        self._run_lock = asyncio.Lock()

    @property
    def mode(self) -> vsettings.ShellMode:
        return self._mode

    async def start(self) -> None:
        if self._processor is not None:
            return

        processor_cls = PROCESSOR_FACTORY.get(self._mode)
        if processor_cls is None:
            raise ValueError(f"Unsupported shell mode: {self._mode}")

        self._processor = processor_cls(
            process_manager=self._pm,
            settings=self._settings,
            default_cwd=self._default_cwd,
            env_overlay=self._env_overlay,
        )
        await self._processor.start()

    async def stop(self) -> None:
        if self._processor is None:
            return
        processor = self._processor
        self._processor = None
        await processor.stop()

    async def run(self, command: str, timeout: Optional[float] = None) -> ShellCommandHandle:
        """
        Run a shell command using the configured processor.

        Ensures:
        - Underlying processor is started on first use.
        - Commands are serialized: concurrent run() calls are queued so that
          only one command is active at a time.
        - Optional per-command timeout is enforced (falling back to
          ShellSettings.default_timeout_s when not provided).
        """
        # Acquire the run lock so only one command runs at a time. The lock is
        # held until this command finishes (or times out), and then released
        # by a background waiter task.
        await self._run_lock.acquire()
        try:
            if self._processor is None:
                await self.start()
            assert self._processor is not None

            if timeout is not None:
                effective_timeout: Optional[float] = timeout
            else:
                effective_timeout = self._settings.default_timeout_s

            inner_handle = await self._processor.run(command)
            handle: ShellCommandHandle = ManagedShellCommand(
                inner=inner_handle,
                timeout=effective_timeout,
            )

            async def _waiter() -> None:
                try:
                    await handle.wait()
                except Exception:
                    # Errors (including asyncio.TimeoutError) are observed by callers of wait().
                    pass
                finally:
                    if self._run_lock.locked():
                        self._run_lock.release()

            asyncio.create_task(_waiter())
            return handle
        except asyncio.CancelledError:
            if self._run_lock.locked():
                self._run_lock.release()
            raise
        except Exception:
            if self._run_lock.locked():
                self._run_lock.release()
            raise


__all__ = ["ShellManager", "ShellCommandHandle"]