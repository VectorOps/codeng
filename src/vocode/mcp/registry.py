from __future__ import annotations

from typing import Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import models as mcp_models


class MCPRegistry:
    def __init__(self, settings: Optional[vocode_settings.MCPSettings]) -> None:
        self._settings = settings
        self._sources: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        if settings is not None and settings.enabled:
            self._sources = self._build_sources(settings)

    def list_sources(self) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        return dict(self._sources)

    def get_source(self, name: str) -> Optional[mcp_models.MCPSourceDescriptor]:
        return self._sources.get(name)

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

        root_settings = None
        if (
            workflow is not None
            and workflow.mcp is not None
            and workflow.mcp.roots is not None
        ):
            root_settings = workflow.mcp.roots
        elif source_settings.roots is not None:
            root_settings = source_settings.roots
        elif self._settings.roots is not None:
            root_settings = self._settings.roots

        if root_settings is not None:
            return self._convert_root_settings(root_settings)

        if (
            isinstance(source_settings, vocode_settings.MCPStdioSourceSettings)
            and project_root_uri is not None
        ):
            return [mcp_models.MCPRootDescriptor(uri=project_root_uri)]

        return []

    def resolve_workflow_tools(
        self,
        workflow: Optional[vocode_settings.WorkflowConfig],
        source_name: str,
        tool_names: list[str],
    ) -> list[str]:
        if workflow is None or workflow.mcp is None or not workflow.mcp.enabled:
            return []
        workflow_mcp = workflow.mcp
        out: list[str] = []
        for tool_name in tool_names:
            if self._is_disabled(workflow_mcp, source_name, tool_name):
                continue
            if self._is_enabled(workflow_mcp, source_name, tool_name):
                out.append(tool_name)
        return out

    def _is_enabled(
        self,
        workflow_mcp: vocode_settings.MCPWorkflowSettings,
        source_name: str,
        tool_name: str,
    ) -> bool:
        for selector in workflow_mcp.tools:
            if selector.source != source_name:
                continue
            if selector.tool == "*" or selector.tool == tool_name:
                return True
        return False

    def _is_disabled(
        self,
        workflow_mcp: vocode_settings.MCPWorkflowSettings,
        source_name: str,
        tool_name: str,
    ) -> bool:
        for selector in workflow_mcp.disabled_tools:
            if selector.source != source_name:
                continue
            if selector.tool == "*" or selector.tool == tool_name:
                return True
        return False

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
