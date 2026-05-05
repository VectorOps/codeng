from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from vocode import settings as vocode_settings
from vocode.mcp.errors import MCPServiceError
from vocode.mcp import models as mcp_models
from vocode.mcp import naming as mcp_naming
from vocode.settings import ToolSpec
from vocode.tools import base as tools_base


def _load_selector_list(value: Any) -> list[vocode_settings.MCPToolSelector]:
    if not isinstance(value, list):
        return []
    selectors: list[vocode_settings.MCPToolSelector] = []
    for item in value:
        if isinstance(item, vocode_settings.MCPToolSelector):
            selectors.append(item)
            continue
        if not isinstance(item, dict):
            continue
        selectors.append(vocode_settings.MCPToolSelector.model_validate(item))
    return selectors


def _load_node_tool_selectors(
    spec: ToolSpec,
) -> tuple[
    list[vocode_settings.MCPToolSelector],
    list[vocode_settings.MCPToolSelector],
]:
    config = spec.config or {}
    return (
        _load_selector_list(config.get("mcp_selectors")),
        _load_selector_list(config.get("mcp_disabled_selectors")),
    )


def _is_node_tool_enabled(
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


def _list_enabled_capability_sources(
    source_names: list[str],
    selectors: list[vocode_settings.MCPToolSelector],
    disabled_selectors: list[vocode_settings.MCPToolSelector],
) -> list[str]:
    if not selectors:
        return list(source_names)
    enabled: list[str] = []
    for source_name in source_names:
        if not _is_node_tool_enabled(
            selectors,
            disabled_selectors,
            source_name,
            "*",
        ):
            continue
        enabled.append(source_name)
    return enabled


@tools_base.ToolFactory.register("mcp_discovery")
class MCPDiscoveryTool(tools_base.BaseTool):
    name = "mcp_discovery"

    def _get_discovery_settings(self) -> vocode_settings.MCPDiscoverySettings:
        if (
            self.prj.settings.mcp is not None
            and self.prj.settings.mcp.discovery is not None
        ):
            return self.prj.settings.mcp.discovery
        return vocode_settings.MCPDiscoverySettings()

    def _tokenize(self, value: str) -> list[str]:
        return [item for item in re.split(r"[^A-Za-z0-9_]+", value.lower()) if item]

    def _score_text(self, query_terms: list[str], value: Optional[str]) -> float:
        if not query_terms or not value:
            return 0.0
        normalized = value.lower()
        tokens = self._tokenize(value)
        if not tokens:
            return 0.0
        score = 0.0
        for term in query_terms:
            if term in normalized:
                score += 1.0
            if term in tokens:
                score += 1.5
            for token in tokens:
                if token.startswith(term):
                    score += 0.75
                    break
        return score / float(len(tokens) + 2)

    def _build_tool_spec(
        self,
        source_name: str,
        descriptor,
    ) -> Dict[str, Any]:
        internal_name = mcp_naming.build_internal_tool_name(
            source_name,
            descriptor.tool_name,
        )
        return {
            "type": "function",
            "function": {
                "name": internal_name,
                "description": descriptor.description or "",
                "parameters": descriptor.input_schema,
            },
        }

    async def run(
        self,
        req: tools_base.ToolReq,
        args: Any,
    ) -> tools_base.ToolTextResponse:
        if self.prj.mcp is None:
            return tools_base.ToolTextResponse(text="[]", data={"tools": []})

        source_name: Optional[str] = None
        query: Optional[str] = None
        max_results: Optional[int] = None
        if isinstance(args, dict):
            raw_source_name = args.get("source")
            if isinstance(raw_source_name, str) and raw_source_name.strip():
                source_name = raw_source_name
            raw_query = args.get("query")
            if isinstance(raw_query, str) and raw_query.strip():
                query = raw_query.strip()
            raw_max_results = args.get("max_results")
            if isinstance(raw_max_results, int) and raw_max_results > 0:
                max_results = raw_max_results

        selectors, disabled_selectors = _load_node_tool_selectors(req.spec)

        discovery_settings = self._get_discovery_settings()
        effective_max_results = max_results or discovery_settings.max_results
        query_terms = self._tokenize(query or "")

        results: List[Dict[str, Any]] = []
        for cached_source_name, descriptors in self.prj.mcp.list_tool_cache().items():
            if source_name is not None and cached_source_name != source_name:
                continue
            for descriptor in descriptors.values():
                if selectors and not _is_node_tool_enabled(
                    selectors,
                    disabled_selectors,
                    cached_source_name,
                    descriptor.tool_name,
                ):
                    continue
                internal_name = mcp_naming.build_internal_tool_name(
                    cached_source_name,
                    descriptor.tool_name,
                )
                hidden = internal_name not in self.prj.tools
                score = 0.0
                if query_terms:
                    score += discovery_settings.name_weight * self._score_text(
                        query_terms,
                        descriptor.tool_name,
                    )
                    score += discovery_settings.title_weight * self._score_text(
                        query_terms,
                        descriptor.title,
                    )
                    score += discovery_settings.description_weight * self._score_text(
                        query_terms,
                        descriptor.description,
                    )
                    schema_terms = " ".join(
                        self._tokenize(
                            json.dumps(descriptor.input_schema, sort_keys=True)
                        )
                    )
                    score += discovery_settings.schema_weight * self._score_text(
                        query_terms,
                        schema_terms,
                    )
                    if score < discovery_settings.min_score:
                        continue
                results.append(
                    {
                        "name": internal_name,
                        "source": cached_source_name,
                        "tool": descriptor.tool_name,
                        "title": descriptor.title,
                        "description": descriptor.description,
                        "hidden": hidden,
                        "score": score,
                        "tool_spec": self._build_tool_spec(
                            cached_source_name,
                            descriptor,
                        ),
                    }
                )

        if query_terms:
            results.sort(
                key=lambda item: (
                    -float(item["score"]),
                    str(item["source"]),
                    str(item["tool"]),
                )
            )
        else:
            results.sort(key=lambda item: (str(item["source"]), str(item["tool"])))
        results = results[:effective_max_results]
        return tools_base.ToolTextResponse(
            text=json.dumps(results),
            data={"tools": results},
        )

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": spec.name,
            "description": "Search workflow-enabled MCP tools and return callable tool specs, including hidden tools omitted from initial LLM tool injection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Optional MCP source name to filter by.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional free-form search query matched against MCP tool names, titles, descriptions, and schemas.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional limit for returned tool matches.",
                    },
                },
                "additionalProperties": False,
            },
        }


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
            prompts = await self._list_prompts(req.spec, source_name)
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
                req.spec,
                source_name,
                prompt_name,
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

    async def _list_prompts(
        self,
        spec: ToolSpec,
        source_name: Optional[str],
    ) -> list[Any]:
        if self.prj.mcp is None:
            return []
        selectors, disabled_selectors = _load_node_tool_selectors(spec)
        source_names = _list_enabled_capability_sources(
            self.prj.mcp.list_prompt_sources(),
            selectors,
            disabled_selectors,
        )
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
        spec: ToolSpec,
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
            for item in await self._list_prompts(spec, None)
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
            resources = await self._list_resources(req.spec, source_name)
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
                req.spec,
                source_name,
                resource_uri,
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

    async def _list_resources(
        self,
        spec: ToolSpec,
        source_name: Optional[str],
    ) -> list[Any]:
        if self.prj.mcp is None:
            return []
        selectors, disabled_selectors = _load_node_tool_selectors(spec)
        source_names = _list_enabled_capability_sources(
            self.prj.mcp.list_resource_sources(),
            selectors,
            disabled_selectors,
        )
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
        spec: ToolSpec,
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
            for item in await self._list_resources(spec, None)
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
            raise tools_base.ToolExecutionError(
                "MCP service is not available",
                error_type=tools_base.ToolExecutionErrorType.protocol,
            )
        try:
            result = await self.prj.mcp.call_tool(
                self.descriptor.source_name,
                self.descriptor.tool_name,
                args if isinstance(args, dict) else {},
            )
        except MCPServiceError as exc:
            raise tools_base.ToolExecutionError(
                str(exc),
                error_type=tools_base.ToolExecutionErrorType.protocol,
            ) from exc
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
        return tools_base.ToolTextResponse(
            text="\n".join(text_parts) or None,
            data=dict(result),
            is_error=bool(result.get("isError", False)),
        )

    async def openapi_spec(self, spec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.descriptor.description or "",
            "parameters": self.descriptor.input_schema,
        }
