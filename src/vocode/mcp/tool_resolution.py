from __future__ import annotations

from typing import Optional

from vocode import settings as vocode_settings


def resolve_workflow_tools(
    workflow: Optional[vocode_settings.WorkflowConfig],
    source_name: str,
    tool_names: list[str],
) -> list[str]:
    if workflow is None or workflow.mcp is None or not workflow.mcp.enabled:
        return []
    workflow_mcp = workflow.mcp
    out: list[str] = []
    for tool_name in tool_names:
        if is_workflow_tool_enabled(workflow_mcp, source_name, tool_name):
            out.append(tool_name)
    return out


def is_workflow_tool_enabled(
    workflow_mcp: vocode_settings.MCPWorkflowSettings,
    source_name: str,
    tool_name: str,
) -> bool:
    if _is_disabled(workflow_mcp, source_name, tool_name):
        return False
    return _is_enabled(workflow_mcp, source_name, tool_name)


def resolve_workflow_source_names(
    settings: vocode_settings.MCPSettings,
    workflow: Optional[vocode_settings.WorkflowConfig],
) -> list[str]:
    if workflow is None or workflow.mcp is None or not workflow.mcp.enabled:
        return []
    source_names: list[str] = []
    seen: set[str] = set()
    for selector in workflow.mcp.tools:
        source_name = selector.source
        if source_name in seen:
            continue
        source = settings.sources.get(source_name)
        if source is None or source.scope.value != "workflow":
            continue
        if _is_disabled(workflow.mcp, source_name, selector.tool):
            continue
        seen.add(source_name)
        source_names.append(source_name)
    return source_names


def _is_enabled(
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
