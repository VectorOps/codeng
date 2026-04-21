from __future__ import annotations

from typing import Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import models as mcp_models
from vocode.mcp import tool_resolution


class MCPRegistry:
    def __init__(self, settings: Optional[vocode_settings.MCPSettings]) -> None:
        self._settings = settings
        self._sources: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        if settings is not None and settings.enabled:
            self._sources = self._build_sources(settings)

    def list_sources(self) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        return dict(self._sources)

    def list_source_names(
        self,
        *,
        scope: Optional[str] = None,
        transport: Optional[mcp_models.MCPTransportKind] = None,
    ) -> list[str]:
        names: list[str] = []
        for name, source in self._sources.items():
            if scope is not None and source.scope != scope:
                continue
            if transport is not None and source.transport != transport:
                continue
            names.append(name)
        return names

    def resolve_workflow_sources(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
    ) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        if self._settings is None:
            return {}
        if workflow is None:
            return self._filter_sources_by_name(
                self.list_source_names(scope="workflow"),
            )
        return self._filter_sources_by_name(
            tool_resolution.resolve_workflow_source_names(self._settings, workflow)
        )

    def get_source(self, name: str) -> Optional[mcp_models.MCPSourceDescriptor]:
        return self._sources.get(name)

    def is_workflow_tool_enabled(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
        source_name: str,
        tool_name: str,
    ) -> bool:
        if workflow is None or workflow.mcp is None or not workflow.mcp.enabled:
            return False
        return tool_resolution.is_workflow_tool_enabled(
            workflow.mcp,
            source_name,
            tool_name,
        )

    def resolve_effective_roots(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
        source_name: str,
        *,
        project_root_uri: Optional[str] = None,
    ) -> list[mcp_models.MCPRootDescriptor]:
        if self._settings is None:
            return []
        source_settings = self._settings.sources.get(source_name)
        if source_settings is None:
            return []
        roots: list[mcp_models.MCPRootDescriptor] = []
        if (
            isinstance(source_settings, vocode_settings.MCPStdioSourceSettings)
            and project_root_uri is not None
        ):
            roots = [mcp_models.MCPRootDescriptor(uri=project_root_uri)]
        if self._settings.roots is not None:
            roots = self._apply_root_settings(roots, self._settings.roots)
        if source_settings.roots is not None:
            roots = self._apply_root_settings(roots, source_settings.roots)
        if (
            workflow is not None
            and workflow.mcp is not None
            and workflow.mcp.roots is not None
        ):
            roots = self._apply_root_settings(roots, workflow.mcp.roots)
        return roots

    def resolve_root_list_changed(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
        source_name: str,
    ) -> bool:
        if self._settings is None:
            return False
        source_settings = self._settings.sources.get(source_name)
        if source_settings is None:
            return False
        list_changed = False
        if self._settings.roots is not None:
            list_changed = self._apply_root_list_changed(
                list_changed,
                self._settings.roots,
            )
        if source_settings.roots is not None:
            list_changed = self._apply_root_list_changed(
                list_changed,
                source_settings.roots,
            )
        if (
            workflow is not None
            and workflow.mcp is not None
            and workflow.mcp.roots is not None
        ):
            list_changed = self._apply_root_list_changed(
                list_changed,
                workflow.mcp.roots,
            )
        return list_changed

    def resolve_workflow_tools(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
        source_name: str,
        tool_names: list[str],
    ) -> list[str]:
        return tool_resolution.resolve_workflow_tools(
            workflow,
            source_name,
            tool_names,
        )

    def _convert_root_settings(
        self,
        root_settings: vocode_settings.MCPRootSettings,
    ) -> list[mcp_models.MCPRootDescriptor]:
        out: list[mcp_models.MCPRootDescriptor] = []
        for item in root_settings.entries:
            if item.uri is None:
                continue
            out.append(mcp_models.MCPRootDescriptor(uri=item.uri, name=item.name))
        return out

    def _apply_root_settings(
        self,
        roots: list[mcp_models.MCPRootDescriptor],
        root_settings: vocode_settings.MCPRootSettings,
    ) -> list[mcp_models.MCPRootDescriptor]:
        incoming = self._convert_root_settings(root_settings)
        if root_settings.merge_mode == vocode_settings.MCPRootMergeMode.replace:
            return incoming
        return self._dedupe_roots([*roots, *incoming])

    def _apply_root_list_changed(
        self,
        list_changed: bool,
        root_settings: vocode_settings.MCPRootSettings,
    ) -> bool:
        if root_settings.merge_mode == vocode_settings.MCPRootMergeMode.replace:
            return root_settings.list_changed
        return list_changed or root_settings.list_changed

    def _dedupe_roots(
        self,
        roots: list[mcp_models.MCPRootDescriptor],
    ) -> list[mcp_models.MCPRootDescriptor]:
        seen: set[str] = set()
        out: list[mcp_models.MCPRootDescriptor] = []
        for item in roots:
            if item.uri in seen:
                continue
            seen.add(item.uri)
            out.append(item)
        return out

    def _filter_sources_by_name(
        self,
        source_names: list[str],
    ) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        out: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        for name in source_names:
            source = self._sources.get(name)
            if source is None:
                continue
            out[name] = source
        return out

    def _build_sources(
        self, settings: vocode_settings.MCPSettings
    ) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        protocol = settings.protocol or vocode_settings.MCPProtocolSettings()
        out: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        for name, source in settings.sources.items():
            roots = []
            if source.roots is not None:
                for item in source.roots.entries:
                    if item.uri is None:
                        continue
                    roots.append(
                        mcp_models.MCPRootDescriptor(uri=item.uri, name=item.name)
                    )
            transport = mcp_models.MCPTransportKind.stdio
            if isinstance(source, vocode_settings.MCPExternalSourceSettings):
                transport = mcp_models.MCPTransportKind.http
            out[name] = mcp_models.MCPSourceDescriptor(
                source_name=name,
                transport=transport,
                scope=source.scope.value,
                startup_timeout_s=protocol.startup_timeout_s,
                shutdown_timeout_s=protocol.shutdown_timeout_s,
                request_timeout_s=protocol.request_timeout_s,
                max_request_timeout_s=protocol.max_request_timeout_s,
                roots=roots,
            )
        return out
