from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Dict, Optional
from .base import ProcessHandle, ProcessBackend, SpawnOptions, EnvPolicy, get_backend


class ProcessManager:
    def __init__(
        self,
        *,
        backend_name: str,
        default_cwd: Path,
        env_policy: Optional[EnvPolicy] = None,
    ) -> None:
        self._default_cwd = default_cwd
        self._env_policy = env_policy or EnvPolicy()
        backend = get_backend(backend_name)
        # Set public property directly (no getattr/hasattr)
        backend.env_policy = self._env_policy
        self._backend: ProcessBackend = backend
        self._procs: Dict[str, ProcessHandle] = {}

    def list(self) -> list[ProcessHandle]:
        return list(self._procs.values())

    def get(self, pid: str) -> Optional[ProcessHandle]:
        return self._procs.get(pid)

    async def spawn(
        self,
        *,
        command: str,
        name: Optional[str] = None,
        cwd: Optional[Path] = None,
        env_overlay: Optional[dict[str, str]] = None,
        shell: bool = True,
        use_pty: bool = False,
    ) -> ProcessHandle:
        opts = SpawnOptions(
            command=command,
            name=name,
            cwd=cwd or self._default_cwd,
            env_overlay=env_overlay,
            shell=shell,
            use_pty=use_pty,
        )
        handle = await self._backend.spawn(opts)
        self._procs[handle.id] = handle
        return handle

    async def shutdown(self, *, grace_s: float = 5.0) -> None:
        # First close stdin on all processes to release write transports
        closes = [h.close_stdin() for h in list(self._procs.values())]
        if closes:
            await asyncio.gather(*closes, return_exceptions=True)
        tasks = [h.terminate(grace_s=grace_s) for h in list(self._procs.values())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        tasks = [h.kill() for h in list(self._procs.values()) if h.alive()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Ensure all processes are fully reaped and transports closed
        waits = [h.wait() for h in list(self._procs.values())]
        if waits:
            await asyncio.gather(*waits, return_exceptions=True)
        self._procs.clear()
