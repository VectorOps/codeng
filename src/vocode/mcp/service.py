from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Any, Dict, Optional

from vocode import settings as vocode_settings
from vocode.auth import TokenCredentialManager
from vocode.logger import logger
from vocode.mcp import auth as mcp_auth
from vocode.mcp import client as mcp_client
from vocode.mcp import converters as mcp_converters
from vocode.mcp import models as mcp_models
from vocode.mcp import naming as mcp_naming
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


@dataclass(frozen=True)
class MCPActiveWorkflowRef:
    workflow_name: str
    run_id: Optional[str]


class MCPService:
    def __init__(
        self,
        settings: Optional[vocode_settings.MCPSettings],
        *,
        credentials: Optional[TokenCredentialManager] = None,
        project_root_uri: Optional[str] = None,
        has_workflow_roots: bool = False,
        has_workflow_roots_list_changed: bool = False,
    ) -> None:
        self._settings = settings
        self._registry = mcp_registry.MCPRegistry(settings)
        self._auth = mcp_auth.MCPAuthManager(settings, credentials=credentials)
        self._log = logger.bind(component="mcp_service")
        self._project_root_uri = project_root_uri
        self._has_workflow_roots = has_workflow_roots
        self._has_workflow_roots_list_changed = has_workflow_roots_list_changed
        self._active_workflow: Optional[vocode_settings.WorkflowConfig] = None
        self._active_workflow_ref: Optional[MCPActiveWorkflowRef] = None
        self._sessions: Dict[str, mcp_client.MCPClientSession] = {}
        self._tool_cache: Dict[str, Dict[str, mcp_models.MCPToolDescriptor]] = {}
        self._tool_refresh_tasks: Dict[str, asyncio.Task[None]] = {}

    def _session_log_fields(self, session: object) -> Dict[str, object]:
        source = None
        try:
            source = session.source
        except AttributeError:
            source = None
        if source is not None:
            return {
                "transport": source.transport,
                "scope": source.scope,
            }
        return {}

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
        cached = self.cache_tool_descriptors(source_name, payloads)
        self._log.info(
            "MCP tool refresh completed",
            source_name=source_name,
            tool_count=len(cached),
        )
        return cached

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

    async def list_resources(
        self,
        source_name: str,
    ) -> list[mcp_models.MCPResourceDescriptor]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        payloads = await session.list_all_resources()
        resources: list[mcp_models.MCPResourceDescriptor] = []
        seen: set[str] = set()
        for payload in payloads:
            try:
                descriptor = mcp_converters.normalize_resource_descriptor(
                    source_name,
                    payload,
                )
            except mcp_converters.MCPConversionError:
                continue
            if descriptor.uri in seen:
                continue
            seen.add(descriptor.uri)
            resources.append(descriptor)
        return resources

    async def read_resource(
        self,
        source_name: str,
        uri: str,
    ) -> Dict[str, object]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        return await session.read_resource(uri)

    async def list_prompts(
        self,
        source_name: str,
    ) -> list[mcp_models.MCPPromptDescriptor]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        payloads = await session.list_all_prompts()
        prompts: list[mcp_models.MCPPromptDescriptor] = []
        seen: set[str] = set()
        for payload in payloads:
            try:
                descriptor = mcp_converters.normalize_prompt_descriptor(
                    source_name,
                    payload,
                )
            except mcp_converters.MCPConversionError:
                continue
            if descriptor.prompt_name in seen:
                continue
            seen.add(descriptor.prompt_name)
            prompts.append(descriptor)
        return prompts

    async def get_prompt(
        self,
        source_name: str,
        prompt_name: str,
        arguments: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        session = self.get_session(source_name)
        if session is None:
            raise MCPServiceError(f"no active session for mcp source: {source_name}")
        return await session.get_prompt(prompt_name, arguments or {})

    def list_prompt_sources(self) -> list[str]:
        return self._list_sources_with_capability("prompts")

    def list_resource_sources(self) -> list[str]:
        return self._list_sources_with_capability("resources")

    def build_project_tools(
        self,
        prj,
        disabled_tool_names: set[str],
        workflow: Optional[vocode_settings.WorkflowConfig] = None,
    ) -> Dict[str, Any]:
        from vocode.tools.mcp_discovery_tool import MCPDiscoveryTool
        from vocode.tools.mcp_get_prompt_tool import MCPGetPromptTool
        from vocode.tools.mcp_read_resource_tool import MCPReadResourceTool
        from vocode.tools.mcp_tool import MCPToolAdapter

        effective_workflow = workflow
        if effective_workflow is None:
            effective_workflow = self._active_workflow
        workflow_mcp = (
            effective_workflow.mcp if effective_workflow is not None else None
        )
        if workflow_mcp is not None and not workflow_mcp.enabled:
            return {}
        out: Dict[str, Any] = {}
        if (
            self._should_enable_discovery_tool(effective_workflow)
            and MCPDiscoveryTool.name not in disabled_tool_names
        ):
            out[MCPDiscoveryTool.name] = MCPDiscoveryTool(prj)
        if (
            self._should_enable_get_prompt_tool(effective_workflow)
            and MCPGetPromptTool.name not in disabled_tool_names
        ):
            out[MCPGetPromptTool.name] = MCPGetPromptTool(prj)
        if (
            self._should_enable_read_resource_tool(effective_workflow)
            and MCPReadResourceTool.name not in disabled_tool_names
        ):
            out[MCPReadResourceTool.name] = MCPReadResourceTool(prj)
        for source_name, descriptors in self.list_tool_cache().items():
            for descriptor in descriptors.values():
                if not self.registry.is_workflow_tool_enabled(
                    effective_workflow,
                    source_name,
                    descriptor.tool_name,
                ):
                    continue
                if self._should_hide_listed_tools(effective_workflow):
                    continue
                internal_name = mcp_naming.build_internal_tool_name(
                    source_name,
                    descriptor.tool_name,
                )
                if internal_name in disabled_tool_names:
                    continue
                out[internal_name] = MCPToolAdapter(
                    prj,
                    descriptor,
                    internal_name,
                )
        return out

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
        self._log.info(
            "MCP session start requested",
            source_name=source_name,
            transport=source.transport,
            scope=source.scope,
            root_count=len(effective_roots),
        )
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
            self._log.info(
                "MCP session started",
                source_name=source_name,
                transport=source.transport,
                scope=source.scope,
                protocol_version=session.state.negotiation.protocol_version,
            )
        except mcp_client.MCPClientError as exc:
            self._sessions.pop(source_name, None)
            self._log.warning(
                "MCP session start failed",
                source_name=source_name,
                transport=source.transport,
                scope=source.scope,
                error=str(exc),
            )
            raise MCPServiceError(
                f"failed to start mcp source {source_name}: {exc}"
            ) from exc
        return session

    async def start_workflow(
        self,
        workflow_name: str,
        workflow: Optional[vocode_settings.WorkflowConfig] = None,
        workflow_run_id: Optional[str] = None,
    ) -> MCPWorkflowSessionChange:
        if self._settings is None or not self._settings.enabled:
            return MCPWorkflowSessionChange([], [])
        self._active_workflow = workflow
        self._active_workflow_ref = MCPActiveWorkflowRef(
            workflow_name=workflow_name,
            run_id=workflow_run_id,
        )
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
        self._log.info(
            "MCP workflow sessions reconciled",
            workflow_name=workflow_name,
            workflow_run_id=workflow_run_id,
            started_sources=started_sources,
            stopped_sources=stopped_sources,
        )
        return MCPWorkflowSessionChange(
            started_sources=started_sources,
            stopped_sources=stopped_sources,
        )

    async def finish_workflow(
        self,
        workflow_name: str,
        keep_sessions: bool = False,
        workflow_run_id: Optional[str] = None,
    ) -> MCPWorkflowSessionChange:
        if self._settings is None or not self._settings.enabled:
            return MCPWorkflowSessionChange([], [])
        if keep_sessions:
            return MCPWorkflowSessionChange([], [])
        active_workflow_ref = self._active_workflow_ref
        if active_workflow_ref is not None:
            if active_workflow_ref.run_id is not None and workflow_run_id is not None:
                if active_workflow_ref.run_id != workflow_run_id:
                    return MCPWorkflowSessionChange([], [])
            elif active_workflow_ref.workflow_name != workflow_name:
                return MCPWorkflowSessionChange([], [])
        self._active_workflow = None
        self._active_workflow_ref = None
        stopped_sources: list[str] = []
        for name in self._list_workflow_session_names():
            await self.close_session(name)
            stopped_sources.append(name)
        await self._reconcile_session_roots()
        self._log.info(
            "MCP workflow sessions finished",
            workflow_name=workflow_name,
            workflow_run_id=workflow_run_id,
            stopped_sources=stopped_sources,
        )
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
        self._log.info(
            "MCP session closing",
            source_name=source_name,
            **self._session_log_fields(session),
        )
        try:
            await session.close()
        except Exception as exc:
            self._log.warning(
                "MCP session close failed",
                source_name=source_name,
                **self._session_log_fields(session),
                error=str(exc),
            )
            return
        self._log.info(
            "MCP session closed",
            source_name=source_name,
            **self._session_log_fields(session),
        )

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
            self._log.info("MCP login requested", source_name=source_name)
            await self._auth.resolve_headers(source_name, source_settings)
            self._log.info("MCP login completed", source_name=source_name)
        except mcp_auth.MCPAuthError as exc:
            self._log.warning(
                "MCP login failed",
                source_name=source_name,
                error=str(exc),
            )
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
        self._log.info("MCP logout completed", source_name=source_name)

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

    def _should_hide_listed_tools(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> bool:
        hidden = False
        if self._settings is not None:
            hidden = self._settings.hide_listed_tools
        if workflow is None or workflow.mcp is None:
            return hidden
        return workflow.mcp.hide_listed_tools

    def _should_enable_discovery_tool(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> bool:
        if self._settings is None:
            return False
        discovery_settings = self._settings.discovery
        if discovery_settings is not None and not discovery_settings.enabled:
            return False
        for source_name, descriptors in self.list_tool_cache().items():
            if not descriptors:
                continue
            for descriptor in descriptors.values():
                if self.registry.is_workflow_tool_enabled(
                    workflow,
                    source_name,
                    descriptor.tool_name,
                ):
                    return True
        return False

    def _should_enable_get_prompt_tool(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> bool:
        if not self._has_enabled_workflow(workflow):
            return False
        return bool(self.list_prompt_sources())

    def _should_enable_read_resource_tool(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> bool:
        if not self._has_enabled_workflow(workflow):
            return False
        return bool(self.list_resource_sources())

    def _has_enabled_workflow(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> bool:
        return (
            workflow is not None and workflow.mcp is not None and workflow.mcp.enabled
        )

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
        self._log.info(
            "MCP tools list changed notification received",
            source_name=source_name,
        )
        self._tool_refresh_tasks[source_name] = asyncio.create_task(
            self._refresh_tools_from_notification(source_name)
        )

    async def _refresh_tools_from_notification(self, source_name: str) -> None:
        task = self._tool_refresh_tasks.get(source_name)
        try:
            await self.refresh_tools(source_name)
        except MCPServiceError as exc:
            self._log.warning(
                "MCP tool refresh from notification failed",
                source_name=source_name,
                error=str(exc),
            )
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
        self._log.info(
            "MCP inactive session dropped",
            source_name=source_name,
            **self._session_log_fields(session),
            last_error=session.state.last_error,
        )

    def _is_session_active(self, session: mcp_client.MCPClientSession) -> bool:
        return (
            session.state.initialized
            and session.state.phase == mcp_models.MCPSessionPhase.operating
        )

    def _list_sources_with_capability(self, capability_name: str) -> list[str]:
        names: list[str] = []
        for source_name, session in self.list_sessions().items():
            capabilities = session.state.negotiation.server_capabilities
            if capability_name == "prompts" and capabilities.prompts:
                names.append(source_name)
            if capability_name == "resources" and capabilities.resources:
                names.append(source_name)
        return names
