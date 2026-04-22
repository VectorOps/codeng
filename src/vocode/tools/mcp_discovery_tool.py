from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from vocode import settings as vocode_settings
from vocode.mcp import naming as mcp_naming
from vocode.settings import ToolSpec
from vocode.tools import base as tools_base


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

        workflow = None
        if self.prj.current_workflow is not None:
            workflow = self.prj.settings.workflows.get(self.prj.current_workflow)
        if (
            workflow is not None
            and workflow.mcp is not None
            and not workflow.mcp.enabled
        ):
            return tools_base.ToolTextResponse(text="[]", data={"tools": []})

        discovery_settings = self._get_discovery_settings()
        effective_max_results = max_results or discovery_settings.max_results
        query_terms = self._tokenize(query or "")

        results: List[Dict[str, Any]] = []
        for cached_source_name, descriptors in self.prj.mcp.list_tool_cache().items():
            if source_name is not None and cached_source_name != source_name:
                continue
            for descriptor in descriptors.values():
                if not self.prj.mcp.registry.is_workflow_tool_enabled(
                    workflow,
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
