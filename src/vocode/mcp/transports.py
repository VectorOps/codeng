from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from vocode.mcp import protocol as mcp_protocol


class MCPTransportError(Exception):
    pass


class MCPStdioTransport:
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
        self._stderr_lines: List[str] = []
        self._stderr_task: Optional[asyncio.Task[None]] = None

    @property
    def stderr_lines(self) -> List[str]:
        return list(self._stderr_lines)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.is_running:
            return
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
            raise MCPTransportError(
                f"stdio transport startup timed out after {self._startup_timeout_s} seconds"
            ) from exc
        if self._proc is None:
            raise MCPTransportError("failed to start stdio transport")
        self._stderr_task = asyncio.create_task(self._collect_stderr())

    async def send(self, message: mcp_protocol.MCPJSONRPCMessage) -> None:
        if not self.is_running or self._proc is None or self._proc.stdin is None:
            raise MCPTransportError("stdio transport is not running")
        payload = message.model_dump_json(exclude_none=True) + "\n"
        self._proc.stdin.write(payload.encode("utf-8"))
        await self._proc.stdin.drain()

    async def receive(self) -> mcp_protocol.MCPJSONRPCMessage:
        if not self.is_running or self._proc is None or self._proc.stdout is None:
            raise MCPTransportError("stdio transport is not running")
        line = await self._proc.stdout.readline()
        if not line:
            raise MCPTransportError(
                "stdio transport closed before a message was received"
            )
        try:
            data = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPTransportError(
                "received invalid JSON from stdio transport"
            ) from exc
        if "method" in data and "id" in data:
            return mcp_protocol.MCPJSONRPCRequest.model_validate(data)
        if "method" in data:
            return mcp_protocol.MCPJSONRPCNotification.model_validate(data)
        if "error" in data:
            return mcp_protocol.MCPJSONRPCErrorResponse.model_validate(data)
        return mcp_protocol.MCPJSONRPCResponse.model_validate(data)

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
        if self._stderr_task is not None:
            await self._stderr_task
            self._stderr_task = None
        self._proc = None

    async def _collect_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            self._stderr_lines.append(line.decode("utf-8", errors="replace"))
