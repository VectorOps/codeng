from __future__ import annotations

import asyncio
import contextlib
import typing

from typing import Any, Dict, Optional

from vocode.mcp import models as mcp_models
from vocode.mcp import protocol as mcp_protocol
from vocode.mcp import transports as mcp_transports


class MCPClientError(Exception):
    pass


class MCPClientSession:
    def __init__(
        self,
        source: mcp_models.MCPSourceDescriptor,
        transport: mcp_transports.MCPHTTPTransport | mcp_transports.MCPStdioTransport,
        *,
        client_name: str = "vocode",
        client_version: str = "0",
        client_capabilities: Optional[mcp_models.MCPClientCapabilities] = None,
        roots: Optional[list[mcp_models.MCPRootDescriptor]] = None,
    ) -> None:
        current_roots = list(roots or source.roots)
        self.source = source.model_copy(update={"roots": current_roots})
        self.transport = transport
        self.protocol = mcp_protocol.MCPProtocolClient()
        self.state = mcp_models.MCPSessionState(source=self.source)
        self._client_name = client_name
        self._client_version = client_version
        self._client_capabilities = (
            client_capabilities or mcp_models.MCPClientCapabilities()
        )
        self._roots = current_roots
        self._notification_handlers: list[
            typing.Callable[[mcp_protocol.MCPJSONRPCNotification], None]
        ] = []
        self._receive_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        await self.transport.start()
        try:
            await self.initialize()
        except Exception:
            await self._mark_disconnected("session initialization failed")
            raise

    def add_notification_handler(
        self,
        handler: typing.Callable[[mcp_protocol.MCPJSONRPCNotification], None],
    ) -> None:
        self._notification_handlers.append(handler)

    def list_roots(self) -> list[mcp_models.MCPRootDescriptor]:
        return list(self._roots)

    async def initialize(self) -> Dict[str, Any]:
        try:
            if not self.transport.is_running:
                await self.transport.start()
            request = self.protocol.create_request(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "clientInfo": {
                        "name": self._client_name,
                        "version": self._client_version,
                    },
                    "capabilities": self._client_capabilities.to_initialize_payload(),
                },
            )
            future = self.protocol.register_pending(request)
            message = await self.transport.request(request)
            if isinstance(message, mcp_protocol.MCPJSONRPCRequest):
                raise MCPClientError("unexpected request received during initialize")
            if isinstance(message, mcp_protocol.MCPJSONRPCNotification):
                raise MCPClientError(
                    "unexpected notification received before initialize response"
                )
            self.protocol.handle_response(message)
            result = await future
            if isinstance(self.transport, mcp_transports.MCPHTTPTransport):
                self.transport.set_protocol_version(result.get("protocolVersion"))
            notification = self.protocol.build_initialized_notification()
            await self.transport.notify(notification)
            self.state = mcp_models.MCPSessionState(
                source=self.source,
                phase=mcp_models.MCPSessionPhase.operating,
                initialized=True,
                negotiation=mcp_models.MCPSessionNegotiation(
                    protocol_version=self.protocol.state.negotiation.protocol_version,
                    client_capabilities=self._client_capabilities,
                    server_capabilities=(
                        self._parse_server_capabilities(
                            result.get("capabilities") or {}
                        )
                    ),
                    server_info=self.protocol.state.negotiation.server_info,
                ),
            )
            self._ensure_receive_loop()
            return result
        except Exception as exc:
            await self._mark_disconnected(str(exc))
            if isinstance(exc, MCPClientError):
                raise
            raise MCPClientError(str(exc)) from exc

    async def close(self) -> None:
        await self._mark_disconnected(self.state.last_error)

    async def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await self.request_with_timeout(method, params=params, timeout_s=None)

    async def request_with_timeout(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self.state.initialized:
            raise MCPClientError("session must be initialized before sending requests")
        try:
            request = self.protocol.create_request(method, params)
            future = self.protocol.register_pending(request)
            if isinstance(self.transport, mcp_transports.MCPHTTPTransport):
                if timeout_s is None:
                    message = await self.transport.request(request)
                else:
                    message = await asyncio.wait_for(
                        self.transport.request(request),
                        timeout_s,
                    )
            else:
                await self.transport.send(request)
                if timeout_s is None:
                    return await future
                return await asyncio.wait_for(
                    future,
                    timeout_s,
                )
            if isinstance(message, mcp_protocol.MCPJSONRPCRequest):
                raise MCPClientError(
                    "unexpected request received while waiting for response"
                )
            if isinstance(message, mcp_protocol.MCPJSONRPCNotification):
                raise MCPClientError(
                    "unexpected notification received while waiting for response"
                )
            self.protocol.handle_response(message)
            return await future
        except TimeoutError as exc:
            self.protocol.drop_pending(request.id)
            await self.transport.notify(
                self.protocol.build_cancel_notification(request.id)
            )
            raise MCPClientError(
                f"request timed out after {timeout_s} seconds"
            ) from exc
        except mcp_transports.MCPTransportError as exc:
            await self._mark_disconnected(str(exc))
            raise MCPClientError(str(exc)) from exc
        except mcp_protocol.MCPProtocolError as exc:
            raise MCPClientError(str(exc)) from exc

    async def list_tools(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.tools:
            raise MCPClientError("server does not advertise tools capability")
        params: Dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("tools/list", params)

    async def list_all_tools(self) -> list[Dict[str, Any]]:
        tools: list[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            result = await self.list_tools(cursor=cursor)
            tools.extend(result.get("tools") or [])
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return tools
            cursor = next_cursor

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.tools:
            raise MCPClientError("server does not advertise tools capability")
        return await self.request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

    async def list_resources(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.resources:
            raise MCPClientError("server does not advertise resources capability")
        params: Dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("resources/list", params)

    async def list_all_resources(self) -> list[Dict[str, Any]]:
        resources: list[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            result = await self.list_resources(cursor=cursor)
            resources.extend(result.get("resources") or [])
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return resources
            cursor = next_cursor

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.resources:
            raise MCPClientError("server does not advertise resources capability")
        return await self.request(
            "resources/read",
            {
                "uri": uri,
            },
        )

    async def list_prompts(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.prompts:
            raise MCPClientError("server does not advertise prompts capability")
        params: Dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("prompts/list", params)

    async def list_all_prompts(self) -> list[Dict[str, Any]]:
        prompts: list[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            result = await self.list_prompts(cursor=cursor)
            prompts.extend(result.get("prompts") or [])
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return prompts
            cursor = next_cursor

    async def get_prompt(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.state.negotiation.server_capabilities.prompts:
            raise MCPClientError("server does not advertise prompts capability")
        return await self.request(
            "prompts/get",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

    async def update_roots(
        self,
        roots: list[mcp_models.MCPRootDescriptor],
    ) -> bool:
        if self._normalize_roots(roots) == self._normalize_roots(self._roots):
            return False
        self._set_roots(roots)
        if not self.state.initialized:
            return True
        capabilities = self.state.negotiation
        if not capabilities.client_capabilities.roots:
            return True
        if not capabilities.server_capabilities.roots:
            return True
        if not capabilities.client_capabilities.roots_list_changed:
            return True
        if not capabilities.server_capabilities.roots_list_changed:
            return True
        try:
            await self.transport.notify(
                mcp_protocol.MCPJSONRPCNotification(
                    method="notifications/roots/list_changed"
                )
            )
        except mcp_transports.MCPTransportError as exc:
            await self._mark_disconnected(str(exc))
            raise MCPClientError(str(exc)) from exc
        return True

    def _parse_server_capabilities(
        self, value: Dict[str, Any]
    ) -> mcp_models.MCPServerCapabilities:
        return mcp_models.MCPServerCapabilities(
            tools=isinstance(value.get("tools"), dict) or bool(value.get("tools")),
            tools_list_changed=bool(
                (value.get("tools") or {}).get("listChanged", False)
            ),
            roots=isinstance(value.get("roots"), dict) or bool(value.get("roots")),
            roots_list_changed=bool(
                (value.get("roots") or {}).get("listChanged", False)
            ),
            prompts=isinstance(value.get("prompts"), dict)
            or bool(value.get("prompts")),
            resources=isinstance(value.get("resources"), dict)
            or bool(value.get("resources")),
        )

    def _ensure_receive_loop(self) -> None:
        if not isinstance(self.transport, mcp_transports.MCPStdioTransport):
            return
        if self._receive_task is not None and not self._receive_task.done():
            return
        self._receive_task = asyncio.create_task(self._run_receive_loop())

    async def _run_receive_loop(self) -> None:
        try:
            while self.transport.is_running:
                message = await self.transport.receive()
                if isinstance(message, mcp_protocol.MCPJSONRPCNotification):
                    self._dispatch_notification(message)
                    continue
                if isinstance(message, mcp_protocol.MCPJSONRPCRequest):
                    await self._handle_request(message)
                    continue
                self.protocol.handle_response(message)
        except asyncio.CancelledError:
            raise
        except mcp_transports.MCPTransportError as exc:
            if self.state.phase != mcp_models.MCPSessionPhase.closed:
                await self._mark_disconnected(str(exc))

    def _dispatch_notification(
        self,
        notification: mcp_protocol.MCPJSONRPCNotification,
    ) -> None:
        for handler in self._notification_handlers:
            handler(notification)

    async def _handle_request(
        self,
        request: mcp_protocol.MCPJSONRPCRequest,
    ) -> None:
        if request.method == "roots/list":
            if self._client_capabilities.roots:
                response: (
                    mcp_protocol.MCPJSONRPCResponse
                    | mcp_protocol.MCPJSONRPCErrorResponse
                ) = mcp_protocol.MCPJSONRPCResponse(
                    id=request.id,
                    result={
                        "roots": [
                            item.model_dump(exclude_none=True) for item in self._roots
                        ]
                    },
                )
            else:
                response = mcp_protocol.MCPJSONRPCErrorResponse(
                    id=request.id,
                    error=mcp_protocol.MCPJSONRPCError(
                        code=int(mcp_protocol.MCPJSONRPCErrorCode.method_not_found),
                        message="roots capability is not enabled for this session",
                    ),
                )
        else:
            response = mcp_protocol.MCPJSONRPCErrorResponse(
                id=request.id,
                error=mcp_protocol.MCPJSONRPCError(
                    code=int(mcp_protocol.MCPJSONRPCErrorCode.method_not_found),
                    message=f"unsupported request method: {request.method}",
                ),
            )
        await self.transport.send(response)

    def _set_roots(self, roots: list[mcp_models.MCPRootDescriptor]) -> None:
        self._roots = list(roots)
        self.source = self.source.model_copy(update={"roots": list(self._roots)})
        self.state = self.state.model_copy(update={"source": self.source})

    def _normalize_roots(
        self,
        roots: list[mcp_models.MCPRootDescriptor],
    ) -> list[tuple[str, Optional[str]]]:
        out: list[tuple[str, Optional[str]]] = []
        for item in roots:
            out.append((item.uri, item.name))
        return out

    async def _mark_disconnected(self, error: Optional[str]) -> None:
        if self.state.phase == mcp_models.MCPSessionPhase.closed:
            if error is not None and self.state.last_error is None:
                self.state = self.state.model_copy(update={"last_error": error})
            return
        receive_task = self._receive_task
        self._receive_task = None
        if receive_task is not None and receive_task is not asyncio.current_task():
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task
        try:
            if self.transport.is_running:
                await self.transport.close()
        finally:
            self.state = mcp_models.MCPSessionState(
                source=self.source,
                phase=mcp_models.MCPSessionPhase.closed,
                initialized=False,
                last_error=error,
            )
