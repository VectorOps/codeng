from __future__ import annotations

from typing import Any, Dict

from vocode.mcp import models as mcp_models
from vocode.tools import base as tools_base


class MCPToolAdapter(tools_base.BaseTool):
    name = "mcp_tool"

    def __init__(
        self,
        prj,
        descriptor: mcp_models.MCPToolDescriptor,
        internal_name: str,
    ) -> None:
        super().__init__(prj)
        self.descriptor = descriptor
        self.name = internal_name

    async def run(
        self,
        req: tools_base.ToolReq,
        args: Any,
    ) -> tools_base.ToolTextResponse:
        if self.prj.mcp is None:
            raise RuntimeError("MCP service is not available")
        result = await self.prj.mcp.call_tool(
            self.descriptor.source_name,
            self.descriptor.tool_name,
            args if isinstance(args, dict) else {},
        )
        content = result.get("content") or []
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        return tools_base.ToolTextResponse(text="\n".join(text_parts) or None)

    async def openapi_spec(self, spec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.descriptor.description or "",
            "parameters": self.descriptor.input_schema,
        }
