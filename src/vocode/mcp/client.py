from __future__ import annotations

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
        transport: mcp_transports.MCPStdioTransport,
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
        await self.transport.send(request)
        message = await self.transport.receive()
        if isinstance(message, mcp_protocol.MCPJSONRPCRequest):
            raise MCPClientError("unexpected request received during initialize")
        if isinstance(message, mcp_protocol.MCPJSONRPCNotification):
            raise MCPClientError(
                "unexpected notification received before initialize response"
            )
        self.protocol.handle_response(message)
        result = await future
        notification = self.protocol.build_initialized_notification()
        await self.transport.send(notification)
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
