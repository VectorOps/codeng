from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic import model_validator


class MCPSessionPhase(str, Enum):
    initializing = "initializing"
    operating = "operating"
    shutdown = "shutdown"
    closed = "closed"


class MCPTransportKind(str, Enum):
    stdio = "stdio"
    http = "http"


class MCPClientCapabilities(BaseModel):
    roots: bool = Field(default=False)
    roots_list_changed: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_roots(self) -> "MCPClientCapabilities":
        if self.roots_list_changed and not self.roots:
            raise ValueError("roots_list_changed requires roots capability")
        return self

    def to_initialize_payload(self) -> Dict[str, object]:
        payload: Dict[str, object] = {}
        if self.roots:
            roots_payload: Dict[str, object] = {}
            if self.roots_list_changed:
                roots_payload["listChanged"] = True
            payload["roots"] = roots_payload
        return payload


class MCPServerCapabilities(BaseModel):
    tools: bool = Field(default=False)
    tools_list_changed: bool = Field(default=False)
    roots: bool = Field(default=False)
    roots_list_changed: bool = Field(default=False)
    prompts: bool = Field(default=False)
    resources: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "MCPServerCapabilities":
        if self.tools_list_changed and not self.tools:
            raise ValueError("tools_list_changed requires tools capability")
        if self.roots_list_changed and not self.roots:
            raise ValueError("roots_list_changed requires roots capability")
        return self


class MCPSessionNegotiation(BaseModel):
    protocol_version: Optional[str] = Field(default=None)
    client_capabilities: MCPClientCapabilities = Field(
        default_factory=MCPClientCapabilities
    )
    server_capabilities: MCPServerCapabilities = Field(
        default_factory=MCPServerCapabilities
    )
    server_info: Dict[str, str] = Field(default_factory=dict)


class MCPRootDescriptor(BaseModel):
    uri: str
    name: Optional[str] = Field(default=None)


class MCPSourceDescriptor(BaseModel):
    source_name: str
    transport: MCPTransportKind
    scope: str
    startup_timeout_s: float
    shutdown_timeout_s: float
    request_timeout_s: float
    max_request_timeout_s: Optional[float] = Field(default=None)
    roots: List[MCPRootDescriptor] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_timeouts(self) -> "MCPSourceDescriptor":
        if not self.source_name.strip():
            raise ValueError("source_name must be non-empty")
        if self.startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be greater than 0")
        if self.shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be greater than 0")
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be greater than 0")
        if self.max_request_timeout_s is not None:
            if self.max_request_timeout_s <= 0:
                raise ValueError("max_request_timeout_s must be greater than 0")
            if self.max_request_timeout_s < self.request_timeout_s:
                raise ValueError(
                    "max_request_timeout_s must be greater than or equal to request_timeout_s"
                )
        return self


class MCPSessionState(BaseModel):
    source: MCPSourceDescriptor
    phase: MCPSessionPhase = Field(default=MCPSessionPhase.initializing)
    negotiation: MCPSessionNegotiation = Field(default_factory=MCPSessionNegotiation)
    initialized: bool = Field(default=False)
    last_error: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def _validate_state(self) -> "MCPSessionState":
        if self.initialized and self.phase == MCPSessionPhase.initializing:
            raise ValueError("initialized session may not remain in initializing phase")
        if self.phase == MCPSessionPhase.operating and not self.initialized:
            raise ValueError("operating session must be initialized")
        return self


class MCPToolDescriptor(BaseModel):
    source_name: str
    tool_name: str
    title: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    input_schema: Dict[str, object] = Field(default_factory=dict)
    annotations: Dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_identity(self) -> "MCPToolDescriptor":
        if not self.source_name.strip():
            raise ValueError("source_name must be non-empty")
        if not self.tool_name.strip():
            raise ValueError("tool_name must be non-empty")
        return self
