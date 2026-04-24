from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional


class MCPProcessError(Exception):
    pass


class MCPStdioProcessManager:
    def __init__(
        self,
        command: str,
        *,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        startup_timeout_s: float = 15.0,
        shutdown_timeout_s: float = 10.0,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = dict(env or {})
        self._cwd = cwd
        self._startup_timeout_s = startup_timeout_s
        self._shutdown_timeout_s = shutdown_timeout_s
        self._proc: Optional[asyncio.subprocess.Process] = None

    @property
    def process(self) -> Optional[asyncio.subprocess.Process]:
        return self._proc

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> asyncio.subprocess.Process:
        if self.is_running and self._proc is not None:
            return self._proc
        try:
            self._proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    self._command,
                    *self._args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(Path(self._cwd)) if self._cwd is not None else None,
                    env=self._env or None,
                ),
                timeout=self._startup_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise MCPProcessError(
                f"stdio process startup timed out after {self._startup_timeout_s} seconds"
            ) from exc
        if self._proc is None:
            raise MCPProcessError("failed to start stdio process")
        return self._proc

    async def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.stdin is not None:
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except Exception:
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._shutdown_timeout_s)
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._shutdown_timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        self._proc = None
