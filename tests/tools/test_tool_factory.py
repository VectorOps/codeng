from __future__ import annotations

import pytest

from vocode import tools as tools_mod
from vocode.settings import ToolSpec
from tests.stub_project import StubProject


@tools_mod.ToolFactory.register("test_decorated_tool")
class _DecoratedTool(tools_mod.BaseTool):
    name = "test_decorated_tool"

    async def run(self, req, args):
        return None

    async def openapi_spec(self, spec: ToolSpec):
        return {}


def test_tool_factory_decorator_and_helpers():
    cls = tools_mod.ToolFactory.get("test_decorated_tool")
    assert cls is _DecoratedTool
 

    project = StubProject()
    tool = cls(project)  # type: ignore[call-arg]
    assert isinstance(tool, _DecoratedTool)
    assert tools_mod.ToolFactory.unregister("test_decorated_tool") is True
    assert tools_mod.ToolFactory.get("test_decorated_tool") is None