from __future__ import annotations

from typing import Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import client as mcp_client
from vocode.mcp import converters as mcp_converters
from vocode.mcp import models as mcp_models
from vocode.mcp import registry as mcp_registry
from vocode.mcp import transports as mcp_transports


class MCPServiceError(Exception):
    pass


class MCPService:
    def __init__(self, settings: Optional[vocode_settings.MCPSettings]) -> None:
        self._settings = settings
        self._registry = mcp_registry.MCPRegistry(settings)
        self._sessions: Dict[str, mcp_client.MCPClientSession] = {}
        self._tool_cache: Dict[str, Dict[str, mcp_models.MCPToolDescriptor]] = {}

    @property
    def registry(self) -> mcp_registry.MCPRegistry:
        return self._registry

    def list_sessions(self) -> Dict[str, mcp_client.MCPClientSession]:
        return dict(self._sessions)

    def list_active_sources(self) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        out: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        for name, session in self._sessions.items():
            out[name] = session.source
        return out

    def get_session(self, source_name: str) -> Optional[mcp_client.MCPClientSession]:
        return self._sessions.get(source_name)

    def get_negotiation(
        self, source_name: str
    ) -> Optional[mcp_models.MCPSessionNegotiation]:
        session = self._sessions.get(source_name)
        if session is None:
            return None
        return session.state.negotiation

    def get_session_state(
        self, source_name: str
    ) -> Optional[mcp_models.MCPSessionState]:
        session = self._sessions.get(source_name)
        if session is None:
            return None
        return session.state

    def list_cached_tools(
        self, source_name: str
    ) -> Dict[str, mcp_models.MCPToolDescriptor]:
        cached = self._tool_cache.get(source_name)
        if cached is None:
            return {}
        return dict(cached)

    def cache_tool_descriptors(
        self,
        source_name: str,
        payloads: list[Dict[str, object]],
    ) -> Dict[str, mcp_models.MCPToolDescriptor]:
        out: Dict[str, mcp_models.MCPToolDescriptor] = {}
        for payload in payloads:
            descriptor = mcp_converters.normalize_tool_descriptor(source_name, payload)
            out[descriptor.tool_name] = descriptor
        self._tool_cache[source_name] = out
        return dict(out)

    def clear_tool_cache(self, source_name: str) -> None:
        self._tool_cache.pop(source_name, None)

    async def start_session(self, source_name: str) -> mcp_client.MCPClientSession:
        existing = self._sessions.get(source_name)
        if existing is not None:
            return existing
        if self._settings is None or not self._settings.enabled:
            raise MCPServiceError("mcp is not enabled")
        source = self._registry.get_source(source_name)
        if source is None:
            raise MCPServiceError(f"unknown mcp source: {source_name}")
        source_settings = self._settings.sources[source_name]
        transport: mcp_transports.MCPHTTPTransport | mcp_transports.MCPStdioTransport
        if isinstance(source_settings, vocode_settings.MCPStdioSourceSettings):
            transport = mcp_transports.MCPStdioTransport(
                source_settings.command,
                args=source_settings.args,
                env=source_settings.env,
                cwd=source_settings.cwd,
                startup_timeout_s=source.startup_timeout_s,
                shutdown_timeout_s=source.shutdown_timeout_s,
            )
        else:
            transport = mcp_transports.MCPHTTPTransport(
                source_settings.url,
                headers=source_settings.headers,
            )
        session = mcp_client.MCPClientSession(source, transport)
        await session.start()
        self._sessions[source_name] = session
        return session

    async def start_workflow(self, workflow_name: str) -> None:
        if self._settings is None or not self._settings.enabled:
            return
        for name, source in self._settings.sources.items():
            if source.scope.value != "workflow":
                continue
            await self.start_session(name)

    async def finish_workflow(self, workflow_name: str) -> None:
        if self._settings is None or not self._settings.enabled:
            return
        names_to_close: list[str] = []
        for name, source in self._settings.sources.items():
            if source.scope.value != "workflow":
                continue
            if name in self._sessions:
                names_to_close.append(name)
        for name in names_to_close:
            await self.close_session(name)

    async def close_session(self, source_name: str) -> None:
        session = self._sessions.pop(source_name, None)
        if session is None:
            return
        self.clear_tool_cache(source_name)
        await session.close()

    async def close_all(self) -> None:
        names = list(self._sessions.keys())
        for name in names:
            await self.close_session(name)
