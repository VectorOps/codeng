from __future__ import annotations

import asyncio
import codecs
import json
import typing
from collections.abc import AsyncIterable
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

from vocode.logger import logger
from vocode.mcp import process_manager as mcp_process_manager
from vocode.mcp import protocol as mcp_protocol


class MCPTransportError(Exception):
    pass


@dataclass(slots=True)
class MCPSSEFrame:
    data: str
    event: Optional[str] = None
    id: Optional[str] = None
    retry: Optional[int] = None


async def iter_sse_frames(lines: AsyncIterable[str]) -> AsyncIterator[MCPSSEFrame]:
    data_lines: List[str] = []
    event: Optional[str] = None
    event_id: Optional[str] = None
    retry: Optional[int] = None
    saw_fields = False

    async for line in lines:
        if line == "":
            if saw_fields:
                yield MCPSSEFrame(
                    data="\n".join(data_lines),
                    event=event,
                    id=event_id,
                    retry=retry,
                )
            data_lines = []
            event = None
            event_id = None
            retry = None
            saw_fields = False
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]

        saw_fields = True
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event = value
        elif field == "id":
            event_id = value
        elif field == "retry":
            try:
                retry = int(value)
            except ValueError:
                pass

    if saw_fields:
        yield MCPSSEFrame(
            data="\n".join(data_lines),
            event=event,
            id=event_id,
            retry=retry,
        )


async def iter_sse_lines(chunks: AsyncIterable[bytes]) -> AsyncIterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffered = ""

    async for chunk in chunks:
        if not chunk:
            continue
        buffered += decoder.decode(chunk)
        while True:
            newline_index = buffered.find("\n")
            if newline_index < 0:
                break
            line = buffered[:newline_index]
            buffered = buffered[newline_index + 1 :]
            yield line.rstrip("\r")

    buffered += decoder.decode(b"", final=True)
    if buffered:
        yield buffered.rstrip("\r")


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
        self._log = logger.bind(
            component="mcp_stdio_transport",
            command=command,
            cwd=cwd,
        )
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
        self._write_lock = asyncio.Lock()

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
            self._log.warning("MCP stdio transport start failed", error=str(exc))
            raise MCPTransportError(str(exc)) from exc
        self._log.info("MCP stdio transport started", args=self._args)
        self._stderr_task = asyncio.create_task(self._collect_stderr())

    async def send(self, message: mcp_protocol.MCPJSONRPCMessage) -> None:
        async with self._write_lock:
            proc = self._process_manager.process
            if not self.is_running or proc is None or proc.stdin is None:
                raise MCPTransportError("stdio transport is not running")
            payload = message.model_dump_json(exclude_none=True) + "\n"
            proc.stdin.write(payload.encode("utf-8"))
            await proc.stdin.drain()

    async def notify(self, message: mcp_protocol.MCPJSONRPCNotification) -> None:
        await self.send(message)

    async def request(
        self,
        message: mcp_protocol.MCPJSONRPCMessage,
    ) -> mcp_protocol.MCPJSONRPCMessage:
        await self.send(message)
        return await self.receive()

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
            self._log.warning("MCP stdio transport close failed", error=str(exc))
            raise MCPTransportError(str(exc)) from exc
        if self._stderr_task is not None:
            await self._stderr_task
            self._stderr_task = None
        self._log.info(
            "MCP stdio transport closed",
            stderr_line_count=len(self._stderr_lines),
        )

    async def _collect_stderr(self) -> None:
        proc = self._process_manager.process
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace")
            self._stderr_lines.append(decoded)
            self._log.warning(
                "MCP stdio transport stderr",
                line=decoded.rstrip("\n"),
            )


class MCPHTTPTransport:
    def __init__(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        auth_token: Optional[str] = None,
        protocol_version: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        auth_challenge_handler: Optional[
            typing.Callable[
                [int, Optional[str], int], typing.Awaitable[Optional[Dict[str, str]]]
            ]
        ] = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._authorization_header = self._headers.pop("Authorization", None)
        self._auth_token = auth_token
        self._protocol_version = protocol_version
        self._session = session
        self._owns_session = session is None
        self._auth_challenge_handler = auth_challenge_handler
        self._log = logger.bind(component="mcp_http_transport", url=url)
        self._incoming_messages: asyncio.Queue[mcp_protocol.MCPJSONRPCMessage] = (
            asyncio.Queue()
        )

    @property
    def is_running(self) -> bool:
        return self._session is not None and not self._session.closed

    async def start(self) -> None:
        if self.is_running:
            return
        self._session = aiohttp.ClientSession()
        self._owns_session = True
        self._log.info("MCP HTTP transport started")

    def set_protocol_version(self, value: Optional[str]) -> None:
        self._protocol_version = value

    async def send(self, message: mcp_protocol.MCPJSONRPCMessage) -> None:
        await self._post_message(message, expect_response=False)

    async def receive(self) -> mcp_protocol.MCPJSONRPCMessage:
        if not self.is_running:
            raise MCPTransportError("http transport is not running")
        return await self._incoming_messages.get()

    async def notify(self, message: mcp_protocol.MCPJSONRPCNotification) -> None:
        await self._post_message(message, expect_response=False)

    async def request(
        self,
        message: mcp_protocol.MCPJSONRPCMessage,
    ) -> mcp_protocol.MCPJSONRPCMessage:
        response_payload = await self._post_message(message, expect_response=True)
        if response_payload is None:
            raise MCPTransportError("http transport returned an empty response")
        if isinstance(response_payload, str):
            return self._parse_jsonrpc_message(response_payload)
        while True:
            streamed_message = await response_payload.get()
            if self._is_matching_response(message, streamed_message):
                return streamed_message
            await self._incoming_messages.put(streamed_message)

    async def _post_message(
        self,
        message: mcp_protocol.MCPJSONRPCMessage,
        *,
        expect_response: bool,
    ) -> Optional[str | asyncio.Queue[mcp_protocol.MCPJSONRPCMessage]]:
        if not self.is_running or self._session is None:
            raise MCPTransportError("http transport is not running")
        auth_attempt = 0
        retry_headers: Dict[str, str] = {}
        next_authorization_header: Optional[str] = None
        while True:
            headers = dict(self._headers)
            headers["Accept"] = "application/json, text/event-stream"
            if self._authorization_header is not None:
                headers["Authorization"] = self._authorization_header
            if self._auth_token is not None:
                headers["Authorization"] = f"Bearer {self._auth_token}"
            if self._protocol_version is not None:
                headers["MCP-Protocol-Version"] = self._protocol_version
            headers.update(retry_headers)
            async with self._session.post(
                self._url,
                json=message.model_dump(exclude_none=True),
                headers=headers,
            ) as response:
                if (
                    response.status in {401, 403}
                    and self._auth_challenge_handler is not None
                ):
                    self._log.info(
                        "MCP HTTP auth challenge received",
                        status_code=response.status,
                        auth_attempt=auth_attempt,
                    )
                    refreshed_headers = await self._auth_challenge_handler(
                        response.status,
                        response.headers.get("WWW-Authenticate"),
                        auth_attempt,
                    )
                    await response.read()
                    if refreshed_headers is not None and auth_attempt < 3:
                        retry_headers = dict(refreshed_headers)
                        next_authorization_header = refreshed_headers.get(
                            "Authorization"
                        )
                        self._log.info(
                            "MCP HTTP auth challenge resolved",
                            status_code=response.status,
                            auth_attempt=auth_attempt,
                        )
                        auth_attempt += 1
                        continue
                if response.status >= 400:
                    text = await response.text()
                    self._log.warning(
                        "MCP HTTP request failed",
                        status_code=response.status,
                    )
                    raise MCPTransportError(
                        f"http transport request failed with status {response.status}: {text}"
                    )
                if next_authorization_header is not None:
                    self._authorization_header = next_authorization_header
                if not expect_response:
                    return None
                if response.headers.get("Content-Type", "").startswith(
                    "text/event-stream"
                ):
                    return await self._consume_sse_response(response, message)
                text = await response.text()
                return text

    def _parse_jsonrpc_message(
        self,
        payload: str,
    ) -> mcp_protocol.MCPJSONRPCMessage:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MCPTransportError(
                "received invalid JSON from http transport"
            ) from exc
        if "method" in data and "id" in data:
            return mcp_protocol.MCPJSONRPCRequest.model_validate(data)
        if "method" in data:
            return mcp_protocol.MCPJSONRPCNotification.model_validate(data)
        if "error" in data:
            return mcp_protocol.MCPJSONRPCErrorResponse.model_validate(data)
        return mcp_protocol.MCPJSONRPCResponse.model_validate(data)

    async def _consume_sse_response(
        self,
        response: aiohttp.ClientResponse,
        request_message: mcp_protocol.MCPJSONRPCMessage,
    ) -> asyncio.Queue[mcp_protocol.MCPJSONRPCMessage]:
        messages: asyncio.Queue[mcp_protocol.MCPJSONRPCMessage] = asyncio.Queue()
        matched = False
        async for frame in iter_sse_frames(
            iter_sse_lines(response.content.iter_chunked(65536))
        ):
            if not frame.data:
                continue
            parsed_message = self._parse_jsonrpc_message(frame.data)
            await messages.put(parsed_message)
            if self._is_matching_response(request_message, parsed_message):
                matched = True
                break
        if not matched:
            raise MCPTransportError(
                "http transport closed SSE stream before sending the request response"
            )
        return messages

    def _is_matching_response(
        self,
        request_message: mcp_protocol.MCPJSONRPCMessage,
        candidate: mcp_protocol.MCPJSONRPCMessage,
    ) -> bool:
        if not isinstance(request_message, mcp_protocol.MCPJSONRPCRequest):
            return False
        if isinstance(candidate, mcp_protocol.MCPJSONRPCRequest):
            return False
        if isinstance(candidate, mcp_protocol.MCPJSONRPCNotification):
            return False
        return candidate.id == request_message.id

    async def close(self) -> None:
        if self._session is None:
            return
        if self._owns_session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._log.info("MCP HTTP transport closed")
