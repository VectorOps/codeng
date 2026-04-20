from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from vocode.mcp import process_manager as mcp_process_manager
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
        self._process_manager = mcp_process_manager.MCPStdioProcessManager(
            command,
            args=self._args,
            env=self._env,
            cwd=cwd,
            startup_timeout_s=startup_timeout_s,
            shutdown_timeout_s=shutdown_timeout_s,
        )
        self._stderr_lines: List[str] = []
        self._stderr_task: Optional[asyncio.Task[None]] = None

    @property
    def stderr_lines(self) -> List[str]:
        return list(self._stderr_lines)

    @property
    def is_running(self) -> bool:
        return self._process_manager.is_running

    async def start(self) -> None:
        if self.is_running:
            return
        try:
            await self._process_manager.start()
        except mcp_process_manager.MCPProcessError as exc:
            raise MCPTransportError(str(exc)) from exc
        self._stderr_task = asyncio.create_task(self._collect_stderr())

    async def send(self, message: mcp_protocol.MCPJSONRPCMessage) -> None:
        proc = self._process_manager.process
        if not self.is_running or proc is None or proc.stdin is None:
            raise MCPTransportError("stdio transport is not running")
        payload = message.model_dump_json(exclude_none=True) + "\n"
        proc.stdin.write(payload.encode("utf-8"))
        await proc.stdin.drain()

    async def receive(self) -> mcp_protocol.MCPJSONRPCMessage:
        proc = self._process_manager.process
        if not self.is_running or proc is None or proc.stdout is None:
            raise MCPTransportError("stdio transport is not running")
        line = await proc.stdout.readline()
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
        try:
            await self._process_manager.close()
        except mcp_process_manager.MCPProcessError as exc:
            raise MCPTransportError(str(exc)) from exc
        if self._stderr_task is not None:
            await self._stderr_task
            self._stderr_task = None

    async def _collect_stderr(self) -> None:
        proc = self._process_manager.process
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            self._stderr_lines.append(line.decode("utf-8", errors="replace"))
