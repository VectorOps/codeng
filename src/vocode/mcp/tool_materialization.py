from __future__ import annotations

from typing import Any, Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import naming as mcp_naming
from vocode.tools.mcp_discovery_tool import MCPDiscoveryTool
from vocode.tools.mcp_get_prompt_tool import MCPGetPromptTool
from vocode.tools.mcp_read_resource_tool import MCPReadResourceTool
from vocode.tools.mcp_tool import MCPToolAdapter


def build_project_tools(
    service,
    prj,
    disabled_tool_names: set[str],
    workflow: Optional[vocode_settings.WorkflowConfig] = None,
) -> Dict[str, Any]:
    effective_workflow = workflow
    if effective_workflow is None:
        effective_workflow = service._active_workflow
    workflow_mcp = effective_workflow.mcp if effective_workflow is not None else None
    if workflow_mcp is not None and not workflow_mcp.enabled:
        return {}
    out: Dict[str, Any] = {}
    if (
        service._should_enable_discovery_tool(effective_workflow)
        and MCPDiscoveryTool.name not in disabled_tool_names
    ):
        out[MCPDiscoveryTool.name] = MCPDiscoveryTool(prj)
    if (
        service._should_enable_get_prompt_tool(effective_workflow)
        and MCPGetPromptTool.name not in disabled_tool_names
    ):
        out[MCPGetPromptTool.name] = MCPGetPromptTool(prj)
    if (
        service._should_enable_read_resource_tool(effective_workflow)
        and MCPReadResourceTool.name not in disabled_tool_names
    ):
        out[MCPReadResourceTool.name] = MCPReadResourceTool(prj)
    for source_name, descriptors in service.list_tool_cache().items():
        for descriptor in descriptors.values():
            if not service.registry.is_workflow_tool_enabled(
                effective_workflow,
                source_name,
                descriptor.tool_name,
            ):
                continue
            if service._should_hide_listed_tools(effective_workflow):
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


def build_node_tools(
    service,
    prj,
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
    *,
    resolution_mode: str,
    hide_listed_tools: bool,
) -> tuple[Dict[str, Any], Dict[str, vocode_settings.ToolSpec]]:
    if resolution_mode == "discovery":
        return {
            MCPDiscoveryTool.name: MCPDiscoveryTool(prj),
        }, {
            MCPDiscoveryTool.name: vocode_settings.ToolSpec(
                name=MCPDiscoveryTool.name,
                enabled=True,
            )
        }

    out_tools: Dict[str, Any] = {}
    out_specs: Dict[str, vocode_settings.ToolSpec] = {}

    if hide_listed_tools:
        return out_tools, out_specs

    for source_name, descriptors in service.list_tool_cache().items():
        for descriptor in descriptors.values():
            if not _is_enabled_for_node(
                selectors,
                disabled_selectors,
                source_name,
                descriptor.tool_name,
            ):
                continue
            internal_name = mcp_naming.build_internal_tool_name(
                source_name,
                descriptor.tool_name,
            )
            out_tools[internal_name] = MCPToolAdapter(
                prj,
                descriptor,
                internal_name,
            )
            out_specs[internal_name] = vocode_settings.ToolSpec(
                name=internal_name,
                enabled=True,
            )

    if _should_enable_get_prompt_tool(service, selectors, disabled_selectors):
        out_tools[MCPGetPromptTool.name] = MCPGetPromptTool(prj)
        out_specs[MCPGetPromptTool.name] = vocode_settings.ToolSpec(
            name=MCPGetPromptTool.name,
            enabled=True,
        )
    if _should_enable_read_resource_tool(service, selectors, disabled_selectors):
        out_tools[MCPReadResourceTool.name] = MCPReadResourceTool(prj)
        out_specs[MCPReadResourceTool.name] = vocode_settings.ToolSpec(
            name=MCPReadResourceTool.name,
            enabled=True,
        )
    return out_tools, out_specs


def _is_enabled_for_node(
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
    source_name: str,
    tool_name: str,
) -> bool:
    for selector in disabled_selectors:
        if selector.source != source_name:
            continue
        if selector.tool == "*" or selector.tool == tool_name:
            return False
    for selector in selectors:
        if selector.source != source_name:
            continue
        if selector.tool == "*" or selector.tool == tool_name:
            return True
    return False


def _should_enable_get_prompt_tool(
    service,
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
) -> bool:
    for source_name in service.list_prompt_sources():
        if _is_enabled_for_node(selectors, disabled_selectors, source_name, "*"):
            return True
    return False


def _should_enable_read_resource_tool(
    service,
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
) -> bool:
    for source_name in service.list_resource_sources():
        if _is_enabled_for_node(selectors, disabled_selectors, source_name, "*"):
            return True
    return False
