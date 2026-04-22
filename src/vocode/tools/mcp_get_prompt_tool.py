from __future__ import annotations

import json
from typing import Any, Optional

from vocode.tools import base as tools_base


class MCPGetPromptTool(tools_base.BaseTool):
    name = "mcp_get_prompt"

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
        prompt_name: Optional[str] = None
        prompt_arguments: dict[str, object] = {}
        if isinstance(args, dict):
            raw_source_name = args.get("source")
            if isinstance(raw_source_name, str) and raw_source_name.strip():
                source_name = raw_source_name
            raw_prompt_name = args.get("name")
            if isinstance(raw_prompt_name, str) and raw_prompt_name.strip():
                prompt_name = raw_prompt_name
            raw_prompt_arguments = args.get("arguments")
            if isinstance(raw_prompt_arguments, dict):
                prompt_arguments = dict(raw_prompt_arguments)
        if prompt_name is None:
            prompts = await self._list_prompts(source_name)
            payload = [
                {
                    "source": item.source_name,
                    "name": item.prompt_name,
                    "title": item.title,
                    "description": item.description,
                    "arguments": [
                        {
                            "name": argument.name,
                            "description": argument.description,
                            "required": argument.required,
                        }
                        for argument in item.arguments
                    ],
                }
                for item in prompts
            ]
            return tools_base.ToolTextResponse(
                text=json.dumps(payload),
                data={"prompts": payload},
            )
        try:
            resolved_source_name = await self._resolve_source_name(
                source_name, prompt_name
            )
            result = await self.prj.mcp.get_prompt(
                resolved_source_name,
                prompt_name,
                prompt_arguments,
            )
        except tools_base.ToolExecutionError:
            raise
        except Exception as exc:
            raise tools_base.ToolExecutionError(
                str(exc),
                error_type=tools_base.ToolExecutionErrorType.protocol,
            ) from exc
        text_parts: list[str] = []
        for message in result.get("messages") or []:
            if not isinstance(message, dict):
                continue
            self._append_content_text(text_parts, message.get("content"))
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
            "description": "List available MCP prompts or get one prompt by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Optional MCP source name. Required only when the same prompt name exists in multiple sources.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional MCP prompt name. If omitted, the tool lists available prompts instead of fetching one.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Optional prompt arguments for prompts/get.",
                        "additionalProperties": True,
                    },
                },
                "additionalProperties": False,
            },
        }

    async def _list_prompts(self, source_name: Optional[str]) -> list[Any]:
        if self.prj.mcp is None:
            return []
        source_names = self.prj.mcp.list_prompt_sources()
        if source_name is not None:
            if source_name not in source_names:
                raise tools_base.ToolExecutionError(
                    f"MCP source does not have prompts capability: {source_name}",
                    error_type=tools_base.ToolExecutionErrorType.protocol,
                )
            source_names = [source_name]
        prompts: list[Any] = []
        for current_source_name in source_names:
            prompts.extend(await self.prj.mcp.list_prompts(current_source_name))
        prompts.sort(key=lambda item: (item.source_name, item.prompt_name))
        return prompts

    async def _resolve_source_name(
        self,
        source_name: Optional[str],
        prompt_name: str,
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
            for item in await self._list_prompts(None)
            if item.prompt_name == prompt_name
        ]
        if not matches:
            raise tools_base.ToolExecutionError(
                f"No MCP prompt found for name: {prompt_name}",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        if len(matches) > 1:
            raise tools_base.ToolExecutionError(
                f"Multiple MCP sources expose prompt {prompt_name}; specify source",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        return matches[0].source_name

    def _append_content_text(self, text_parts: list[str], content: Any) -> None:
        if isinstance(content, str):
            text_parts.append(content)
            return
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                text_parts.append(text)
            return
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
