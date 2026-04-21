from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import auth as mcp_auth
from vocode.mcp import client as mcp_client
from vocode.mcp import converters as mcp_converters
from vocode.mcp import models as mcp_models
from vocode.mcp import registry as mcp_registry
from vocode.mcp import transports as mcp_transports


class MCPServiceError(Exception):
    pass


@dataclass(frozen=True)
class MCPWorkflowSessionChange:
    started_sources: list[str]
    stopped_sources: list[str]


@dataclass(frozen=True)
class MCPAuthorizationStatus:
    source_name: str
    has_token: bool
    session_active: bool


class MCPService:
    def __init__(
        self,
        settings: Optional[vocode_settings.MCPSettings],
        *,
        credentials: Optional[mcp_auth.MCPTokenManager] = None,
        project_root_uri: Optional[str] = None,
        has_workflow_roots: bool = False,
        has_workflow_roots_list_changed: bool = False,
    ) -> None:
        self._settings = settings
        self._registry = mcp_registry.MCPRegistry(settings)
        self._auth = mcp_auth.MCPAuthManager(settings, credentials=credentials)
        self._project_root_uri = project_root_uri
        self._has_workflow_roots = has_workflow_roots
        self._has_workflow_roots_list_changed = has_workflow_roots_list_changed
        self._active_workflow: Optional[vocode_settings.WorkflowConfig] = None
        self._sessions: Dict[str, mcp_client.MCPClientSession] = {}
        self._tool_cache: Dict[str, Dict[str, mcp_models.MCPToolDescriptor]] = {}
        self._tool_refresh_tasks: Dict[str, asyncio.Task[None]] = {}

    @property
    def registry(self) -> mcp_registry.MCPRegistry:
        return self._registry

    def list_sessions(self) -> Dict[str, mcp_client.MCPClientSession]:
        self._prune_inactive_sessions()
        return dict(self._sessions)

    def list_active_sources(self) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        out: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        for name, session in self.list_sessions().items():
            out[name] = session.source
        return out

    def get_session(self, source_name: str) -> Optional[mcp_client.MCPClientSession]:
        self._drop_inactive_session(source_name)
        return self._sessions.get(source_name)

    def get_negotiation(
        self, source_name: str
    ) -> Optional[mcp_models.MCPSessionNegotiation]:
        session = self.get_session(source_name)
        if session is None:
            return None
        return session.state.negotiation

    def get_session_state(
        self, source_name: str
    ) -> Optional[mcp_models.MCPSessionState]:
        session = self.get_session(source_name)
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

    def list_tool_cache(self) -> Dict[str, Dict[str, mcp_models.MCPToolDescriptor]]:
        out: Dict[str, Dict[str, mcp_models.MCPToolDescriptor]] = {}
        for source_name, descriptors in self._tool_cache.items():
            out[source_name] = dict(descriptors)
        return out

    def cache_tool_descriptors(
        self,
        source_name: str,
        payloads: list[Dict[str, object]],
    ) -> Dict[str, mcp_models.MCPToolDescriptor]:
        normalized: Dict[str, mcp_models.MCPToolDescriptor] = {}
        duplicate_names: set[str] = set()
        for payload in payloads:
            try:
                descriptor = mcp_converters.normalize_tool_descriptor(
                    source_name,
                    payload,
                )
            except mcp_converters.MCPConversionError:
                continue
            if descriptor.tool_name in duplicate_names:
                continue
            if descriptor.tool_name in normalized:
                normalized.pop(descriptor.tool_name, None)
                duplicate_names.add(descriptor.tool_name)
                continue
            normalized[descriptor.tool_name] = descriptor
        self._tool_cache[source_name] = normalized
        return dict(normalized)

    def clear_tool_cache(self, source_name: str) -> None:
        self._tool_cache.pop(source_name, None)

    async def refresh_tools(
        self,
        source_name: str,
    ) -> Dict[str, mcp_models.MCPToolDescriptor]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        payloads = await session.list_all_tools()
        return self.cache_tool_descriptors(source_name, payloads)

    async def call_tool(
        self,
        source_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        return await session.call_tool(tool_name, arguments or {})

    async def start_session(self, source_name: str) -> mcp_client.MCPClientSession:
        existing = self.get_session(source_name)
        if existing is not None:
            return existing
        if self._settings is None or not self._settings.enabled:
            raise MCPServiceError("mcp is not enabled")
        source = self._registry.get_source(source_name)
        if source is None:
            raise MCPServiceError(f"unknown mcp source: {source_name}")
        effective_roots = self._resolve_effective_roots(source_name)
        source = source.model_copy(update={"roots": effective_roots})
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
            headers = dict(source_settings.headers)
            auth_challenge_handler = None
            headers.update(
                await self._auth.resolve_headers(
                    source_name,
                    source_settings,
                )
            )
            if source_settings.auth is not None and source_settings.auth.enabled:

                async def _handle_auth_challenge(
                    status_code: int,
                    www_authenticate: Optional[str],
                    step_up_attempt: int,
                ) -> Optional[Dict[str, str]]:
                    return await self._auth.resolve_headers_for_challenge(
                        source_name,
                        source_settings,
                        status_code=status_code,
                        www_authenticate=www_authenticate,
                        step_up_attempt=step_up_attempt,
                    )

                auth_challenge_handler = _handle_auth_challenge
            transport = mcp_transports.MCPHTTPTransport(
                source_settings.url,
                headers=headers,
                auth_challenge_handler=auth_challenge_handler,
            )
        session = mcp_client.MCPClientSession(
            source,
            transport,
            client_capabilities=self._build_client_capabilities(source_name),
            roots=effective_roots,
        )
        session.add_notification_handler(
            lambda notification: self._on_session_notification(
                source_name,
                notification,
            )
        )
        self._sessions[source_name] = session
        try:
            await session.start()
        except mcp_client.MCPClientError as exc:
            self._sessions.pop(source_name, None)
            raise MCPServiceError(
                f"failed to start mcp source {source_name}: {exc}"
            ) from exc
        return session

    async def start_workflow(
        self,
        workflow_name: str,
        workflow: Optional[vocode_settings.WorkflowConfig] = None,
    ) -> MCPWorkflowSessionChange:
        if self._settings is None or not self._settings.enabled:
            return MCPWorkflowSessionChange([], [])
        self._active_workflow = workflow
        desired_names = self._resolve_workflow_source_names(workflow)
        current_names = self._list_workflow_session_names()
        started_sources: list[str] = []
        stopped_sources: list[str] = []
        for name in current_names:
            if name in desired_names:
                continue
            await self.close_session(name)
            stopped_sources.append(name)
        for name in desired_names:
            if name in current_names:
                continue
            await self.start_session(name)
            started_sources.append(name)
        await self._reconcile_session_roots()
        return MCPWorkflowSessionChange(
            started_sources=started_sources,
            stopped_sources=stopped_sources,
        )

    async def finish_workflow(
        self,
        workflow_name: str,
        keep_sessions: bool = False,
    ) -> MCPWorkflowSessionChange:
        if self._settings is None or not self._settings.enabled:
            return MCPWorkflowSessionChange([], [])
        if keep_sessions:
            return MCPWorkflowSessionChange([], [])
        self._active_workflow = None
        stopped_sources: list[str] = []
        for name in self._list_workflow_session_names():
            await self.close_session(name)
            stopped_sources.append(name)
        await self._reconcile_session_roots()
        return MCPWorkflowSessionChange([], stopped_sources)

    async def close_session(self, source_name: str) -> None:
        refresh_task = self._tool_refresh_tasks.pop(source_name, None)
        if refresh_task is not None:
            try:
                refresh_task.cancel()
            except Exception:
                refresh_task = None
        if refresh_task is not None:
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        session = self._sessions.pop(source_name, None)
        if session is None:
            return
        self.clear_tool_cache(source_name)
        try:
            await session.close()
        except Exception:
            return

    async def close_all(self) -> None:
        names = list(self._sessions.keys())
        for name in names:
            await self.close_session(name)

    async def authorization_status(self, source_name: str) -> MCPAuthorizationStatus:
        source = self._registry.get_source(source_name)
        if source is None:
            raise MCPServiceError(f"unknown mcp source: {source_name}")
        source_settings = None
        if self._settings is not None:
            source_settings = self._settings.sources.get(source_name)
        has_token = False
        if (
            source_settings is not None
            and isinstance(source_settings, vocode_settings.MCPExternalSourceSettings)
            and source_settings.auth is not None
            and source_settings.auth.enabled
        ):
            has_token = await self._auth.has_stored_token(
                source_name, source_settings.url
            )
        return MCPAuthorizationStatus(
            source_name=source_name,
            has_token=has_token,
            session_active=source_name in self._sessions,
        )

    async def login(self, source_name: str) -> None:
        if self._settings is None or not self._settings.enabled:
            raise MCPServiceError("mcp is not enabled")
        source_settings = self._settings.sources.get(source_name)
        if source_settings is None:
            raise MCPServiceError(f"unknown mcp source: {source_name}")
        if not isinstance(source_settings, vocode_settings.MCPExternalSourceSettings):
            raise MCPServiceError(f"mcp source {source_name} is not an external source")
        try:
            await self._auth.resolve_headers(source_name, source_settings)
        except mcp_auth.MCPAuthError as exc:
            raise MCPServiceError(
                f"failed to authenticate mcp source {source_name}: {exc}"
            ) from exc

    async def logout(self, source_name: str) -> None:
        if self._settings is None or not self._settings.enabled:
            raise MCPServiceError("mcp is not enabled")
        source_settings = self._settings.sources.get(source_name)
        if source_settings is None:
            raise MCPServiceError(f"unknown mcp source: {source_name}")
        if not isinstance(source_settings, vocode_settings.MCPExternalSourceSettings):
            raise MCPServiceError(f"mcp source {source_name} is not an external source")
        await self._auth.clear_token(source_name, source_settings.url)

    def _list_workflow_session_names(self) -> list[str]:
        names: list[str] = []
        for name, session in self._sessions.items():
            if session.source.scope != "workflow":
                continue
            names.append(name)
        return names

    def _resolve_workflow_source_names(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> list[str]:
        return list(self._registry.resolve_workflow_sources(workflow).keys())

    def _on_session_notification(
        self,
        source_name: str,
        notification,
    ) -> None:
        if notification.method != "notifications/tools/list_changed":
            return
        session = self.get_session(source_name)
        if session is None:
            return
        if not session.state.negotiation.server_capabilities.tools_list_changed:
            return
        refresh_task = self._tool_refresh_tasks.get(source_name)
        if refresh_task is not None and not refresh_task.done():
            return
        self._tool_refresh_tasks[source_name] = asyncio.create_task(
            self._refresh_tools_from_notification(source_name)
        )

    async def _refresh_tools_from_notification(self, source_name: str) -> None:
        task = self._tool_refresh_tasks.get(source_name)
        try:
            await self.refresh_tools(source_name)
        except MCPServiceError:
            return
        finally:
            current = self._tool_refresh_tasks.get(source_name)
            if current is task:
                self._tool_refresh_tasks.pop(source_name, None)

    def _resolve_effective_roots(
        self,
        source_name: str,
    ) -> list[mcp_models.MCPRootDescriptor]:
        return self._registry.resolve_effective_roots(
            self._active_workflow,
            source_name,
            project_root_uri=self._project_root_uri,
        )

    def _build_client_capabilities(
        self,
        source_name: str,
    ) -> mcp_models.MCPClientCapabilities:
        roots = bool(self._resolve_effective_roots(source_name))
        roots_list_changed = False
        if roots:
            roots_list_changed = self._registry.resolve_root_list_changed(
                self._active_workflow,
                source_name,
            )
        return mcp_models.MCPClientCapabilities(
            roots=roots,
            roots_list_changed=roots_list_changed,
        )

    async def _reconcile_session_roots(self) -> None:
        for source_name, session in list(self._sessions.items()):
            if not self._is_session_active(session):
                self._drop_inactive_session(source_name)
                continue
            desired_capabilities = self._build_client_capabilities(source_name)
            current_capabilities = session.state.negotiation.client_capabilities
            if current_capabilities.model_dump() != desired_capabilities.model_dump():
                await self.close_session(source_name)
                try:
                    session = await self.start_session(source_name)
                except MCPServiceError:
                    continue
                if session.state.negotiation.server_capabilities.tools:
                    try:
                        await self.refresh_tools(source_name)
                    except mcp_client.MCPClientError:
                        pass
                continue
            try:
                await session.update_roots(self._resolve_effective_roots(source_name))
            except mcp_client.MCPClientError:
                await self.close_session(source_name)

    def _prune_inactive_sessions(self) -> None:
        for source_name in list(self._sessions.keys()):
            self._drop_inactive_session(source_name)

    def _drop_inactive_session(self, source_name: str) -> None:
        session = self._sessions.get(source_name)
        if session is None or self._is_session_active(session):
            return
        refresh_task = self._tool_refresh_tasks.pop(source_name, None)
        if refresh_task is not None:
            refresh_task.cancel()
        self._sessions.pop(source_name, None)
        self.clear_tool_cache(source_name)

    def _is_session_active(self, session: mcp_client.MCPClientSession) -> bool:
        return (
            session.state.initialized
            and session.state.phase == mcp_models.MCPSessionPhase.operating
        )
