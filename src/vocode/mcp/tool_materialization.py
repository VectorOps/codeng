from __future__ import annotations

from typing import Any, Dict

from vocode import settings as vocode_settings
from vocode.mcp.tools import MCPDiscoveryTool
from vocode.mcp.tools import MCPGetPromptTool
from vocode.mcp.tools import MCPReadResourceTool
from vocode.mcp.tools import MCPToolAdapter
from vocode.mcp import naming as mcp_naming


def _build_node_tool_config(
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
    *,
    hide_listed_tools: bool,
) -> Dict[str, Any]:
    return {
        "mcp_selectors": [selector.model_dump(mode="json") for selector in selectors],
        "mcp_disabled_selectors": [
            selector.model_dump(mode="json") for selector in disabled_selectors
        ],
        "mcp_hide_listed_tools": hide_listed_tools,
    }


def build_node_tools(
    service,
    prj,
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
    *,
    resolution_mode: str,
    hide_listed_tools: bool,
) -> tuple[Dict[str, Any], Dict[str, vocode_settings.ToolSpec]]:
    tool_config = _build_node_tool_config(
        selectors,
        disabled_selectors,
        hide_listed_tools=hide_listed_tools,
    )
    if resolution_mode == "discovery":
        return {
            MCPDiscoveryTool.name: MCPDiscoveryTool(prj),
        }, {
            MCPDiscoveryTool.name: vocode_settings.ToolSpec(
                name=MCPDiscoveryTool.name,
                enabled=True,
                config=tool_config,
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
            config=tool_config,
        )
    if _should_enable_read_resource_tool(service, selectors, disabled_selectors):
        out_tools[MCPReadResourceTool.name] = MCPReadResourceTool(prj)
        out_specs[MCPReadResourceTool.name] = vocode_settings.ToolSpec(
            name=MCPReadResourceTool.name,
            enabled=True,
            config=tool_config,
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
