from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field

from vocode.mcp import models as mcp_models


class MCPJSONRPCErrorCode(int, Enum):
    parse_error = -32700
    invalid_request = -32600
    method_not_found = -32601
    invalid_params = -32602
    internal_error = -32603


class MCPJSONRPCError(BaseModel):
    code: int
    message: str
    data: Optional[Dict[str, Any]] = Field(default=None)


class MCPJSONRPCRequest(BaseModel):
    jsonrpc: str = Field(default="2.0")
    id: Union[int, str]
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)


class MCPJSONRPCNotification(BaseModel):
    jsonrpc: str = Field(default="2.0")
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)


class MCPJSONRPCResponse(BaseModel):
    jsonrpc: str = Field(default="2.0")
    id: Union[int, str]
    result: Dict[str, Any] = Field(default_factory=dict)


class MCPJSONRPCErrorResponse(BaseModel):
    jsonrpc: str = Field(default="2.0")
    id: Union[int, str]
    error: MCPJSONRPCError


MCPJSONRPCMessage = Union[
    MCPJSONRPCRequest,
    MCPJSONRPCNotification,
    MCPJSONRPCResponse,
    MCPJSONRPCErrorResponse,
]


class MCPProtocolError(Exception):
    pass


class MCPProtocolState(BaseModel):
    initialized: bool = Field(default=False)
    initialize_request_sent: bool = Field(default=False)
    initialized_notification_sent: bool = Field(default=False)
    negotiation: mcp_models.MCPSessionNegotiation = Field(
        default_factory=mcp_models.MCPSessionNegotiation
    )


class MCPPendingRequest:
    def __init__(self, request: MCPJSONRPCRequest) -> None:
        self.request = request
        self.future: asyncio.Future[Dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )


class MCPProtocolClient:
    def __init__(self) -> None:
        self._next_id = 0
        self._pending: Dict[Union[int, str], MCPPendingRequest] = {}
        self.state = MCPProtocolState()

    def next_request_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def create_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> MCPJSONRPCRequest:
        if not self.state.initialized and method != "initialize":
            raise MCPProtocolError("initialize must be the first protocol request")
        request = MCPJSONRPCRequest(
            id=self.next_request_id(),
            method=method,
            params=params or {},
        )
        if method == "initialize":
            self.state.initialize_request_sent = True
        return request

    def register_pending(
        self, request: MCPJSONRPCRequest
    ) -> asyncio.Future[Dict[str, Any]]:
        pending = MCPPendingRequest(request)
        self._pending[request.id] = pending
        return pending.future

    def pending_count(self) -> int:
        return len(self._pending)

    def build_initialized_notification(self) -> MCPJSONRPCNotification:
        if not self.state.initialize_request_sent:
            raise MCPProtocolError(
                "initialized notification requires a prior initialize request"
            )
        if not self.state.initialized:
            raise MCPProtocolError(
                "initialized notification requires a successful initialize response"
            )
        self.state.initialized_notification_sent = True
        return MCPJSONRPCNotification(method="notifications/initialized")

    def handle_response(
        self,
        response: Union[MCPJSONRPCResponse, MCPJSONRPCErrorResponse],
    ) -> None:
        pending = self._pending.pop(response.id, None)
        if pending is None:
            raise MCPProtocolError(f"unknown response id: {response.id!r}")
        if isinstance(response, MCPJSONRPCErrorResponse):
            pending.future.set_exception(MCPProtocolError(response.error.message))
            return
        if pending.request.method == "initialize":
            self.state.initialized = True
            self.state.negotiation = mcp_models.MCPSessionNegotiation(
                protocol_version=response.result.get("protocolVersion"),
                server_info=response.result.get("serverInfo") or {},
            )
        pending.future.set_result(response.result)

    async def wait_for_response(
        self,
        request: MCPJSONRPCRequest,
        timeout_s: float,
    ) -> Dict[str, Any]:
        if request.id not in self._pending:
            raise MCPProtocolError(f"request id is not pending: {request.id!r}")
        try:
            return await asyncio.wait_for(self._pending[request.id].future, timeout_s)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request.id, None)
            raise MCPProtocolError(
                f"request timed out after {timeout_s} seconds"
            ) from exc
