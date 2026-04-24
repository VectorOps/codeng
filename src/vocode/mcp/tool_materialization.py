from __future__ import annotations

from typing import Any, Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import naming as mcp_naming


def build_project_tools(
    service,
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
