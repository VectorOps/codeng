from __future__ import annotations

import json
from typing import Any, Optional

from vocode.tools import base as tools_base


class MCPReadResourceTool(tools_base.BaseTool):
    name = "mcp_read_resource"

    async def run(
        self,
        req: tools_base.ToolReq,
        args: Any,
    ) -> tools_base.ToolTextResponse:
        if self.prj.mcp is None:
            raise tools_base.ToolExecutionError(
                "MCP service is not available",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        source_name: Optional[str] = None
        resource_uri: Optional[str] = None
        if isinstance(args, dict):
            raw_source_name = args.get("source")
            if isinstance(raw_source_name, str) and raw_source_name.strip():
                source_name = raw_source_name
            raw_resource_uri = args.get("uri")
            if isinstance(raw_resource_uri, str) and raw_resource_uri.strip():
                resource_uri = raw_resource_uri
        if resource_uri is None:
            resources = await self._list_resources(source_name)
            payload = [
                {
                    "source": item.source_name,
                    "uri": item.uri,
                    "name": item.name,
                    "title": item.title,
                    "description": item.description,
                    "mime_type": item.mime_type,
                }
                for item in resources
            ]
            return tools_base.ToolTextResponse(
                text=json.dumps(payload),
                data={"resources": payload},
            )
        try:
            resolved_source_name = await self._resolve_source_name(
                source_name, resource_uri
            )
            result = await self.prj.mcp.read_resource(
                resolved_source_name, resource_uri
            )
        except tools_base.ToolExecutionError:
            raise
        except Exception as exc:
            raise tools_base.ToolExecutionError(
                str(exc),
                error_type=tools_base.ToolExecutionErrorType.protocol,
            ) from exc
        text_parts: list[str] = []
        for item in result.get("contents") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        text = "\n".join(text_parts)
        if not text:
            text = json.dumps(result)
        return tools_base.ToolTextResponse(
            text=text,
            data=dict(result),
        )

    async def openapi_spec(self, spec) -> dict[str, Any]:
        return {
            "name": spec.name,
            "description": "List available MCP resources or read one resource by URI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Optional MCP source name. Required only when the same resource URI exists in multiple sources.",
                    },
                    "uri": {
                        "type": "string",
                        "description": "Optional MCP resource URI. If omitted, the tool lists available resources instead of reading one.",
                    },
                },
                "additionalProperties": False,
            },
        }

    async def _list_resources(self, source_name: Optional[str]) -> list[Any]:
        if self.prj.mcp is None:
            return []
        source_names = self.prj.mcp.list_resource_sources()
        if source_name is not None:
            if source_name not in source_names:
                raise tools_base.ToolExecutionError(
                    f"MCP source does not have resources capability: {source_name}",
                    error_type=tools_base.ToolExecutionErrorType.protocol,
                )
            source_names = [source_name]
        resources: list[Any] = []
        for current_source_name in source_names:
            resources.extend(await self.prj.mcp.list_resources(current_source_name))
        resources.sort(key=lambda item: (item.source_name, item.uri))
        return resources

    async def _resolve_source_name(
        self,
        source_name: Optional[str],
        resource_uri: str,
    ) -> str:
        if self.prj.mcp is None:
            raise tools_base.ToolExecutionError(
                "MCP service is not available",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        if source_name is not None:
            return source_name
        matches = [
            item
            for item in await self._list_resources(None)
            if item.uri == resource_uri
        ]
        if not matches:
            raise tools_base.ToolExecutionError(
                f"No MCP resource found for uri: {resource_uri}",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        if len(matches) > 1:
            raise tools_base.ToolExecutionError(
                f"Multiple MCP sources expose resource uri {resource_uri}; specify source",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        return matches[0].source_name
