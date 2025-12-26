from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING, Union
from pydantic import BaseModel

from knowlt.tools.base import BaseTool as KnowBaseTool, ToolRegistry as KnowToolRegistry
from ..tools import BaseTool, register_tool, ToolTextResponse
from ..settings import ToolSpec

if TYPE_CHECKING:
    from vocode.project import Project


class _KnowToolWrapper(BaseTool):
    """A wrapper to make a know tool compatible with the vocode tool system."""

    def __init__(self, prj: "Project", know_tool: KnowBaseTool):
        super().__init__(prj)
        self._know_tool = know_tool
        self.name = self._know_tool.tool_name
        # Propagate input/output model types from the Know tool
        self.input_model = self._know_tool.tool_input

    async def openapi_spec(self, spec: ToolSpec) -> dict[str, Any]:
        return await self._know_tool.get_openai_schema()

    async def run(self, spec: ToolSpec, args: Any) -> ToolTextResponse:
        try:
            # Execute the knowlt tool directly (async).
            result = await self._know_tool.execute(args)
            return ToolTextResponse(text=result if result is not None else None)
        except Exception as e:
            # Return a structured error instead of propagating.
            return ToolTextResponse(
                text=json.dumps({"error": f"Know tool '{self.name}' failed: {e}"})
            )


def convert_know_tool(prj: "Project", tool: KnowBaseTool):
    return _KnowToolWrapper(prj, tool)
