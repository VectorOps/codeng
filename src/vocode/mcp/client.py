from __future__ import annotations

import asyncio

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
    ) -> None:
        self.source = source
        self.transport = transport
        self.protocol = mcp_protocol.MCPProtocolClient()
        self.state = mcp_models.MCPSessionState(source=source)
        self._client_name = client_name
        self._client_version = client_version
        self._client_capabilities = (
            client_capabilities or mcp_models.MCPClientCapabilities()
        )

    async def start(self) -> None:
        await self.transport.start()
        await self.initialize()

    async def initialize(self) -> Dict[str, Any]:
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
                "capabilities": self._client_capabilities.model_dump(exclude_none=True),
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
                    self._parse_server_capabilities(result.get("capabilities") or {})
                ),
                server_info=self.protocol.state.negotiation.server_info,
            ),
        )
        return result

    async def close(self) -> None:
        await self.transport.close()
        self.state = mcp_models.MCPSessionState(
            source=self.source,
            phase=mcp_models.MCPSessionPhase.closed,
            initialized=False,
            negotiation=self.state.negotiation,
        )

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
                    message = await self.transport.receive()
                else:
                    message = await asyncio.wait_for(
                        self.transport.receive(),
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
