from __future__ import annotations

import json

import pytest

from vocode import state as vocode_state
from vocode.settings import ToolSpec
from vocode.tools import ToolFactory, base as tools_base
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_update_plan_tool_merge_and_replace() -> None:
    project = StubProject()
    ToolClass = ToolFactory.get("update_plan")
    assert ToolClass is not None
    tool = ToolClass(project)  # type: ignore[call-arg]

    execution = vocode_state.WorkflowExecution(workflow_name="test")
    tool_req = tools_base.ToolReq(
        execution=execution, spec=ToolSpec(name="update_plan")
    )

    resp1 = await tool.run(
        tool_req,
        {
            "merge": False,
            "todos": [
                {"id": "step-1", "title": "First", "status": "pending"},
                {"id": "step-2", "title": "Second", "status": "in_progress"},
            ],
        },
    )
    assert resp1 is not None
    data1 = json.loads(resp1.text or "{}")
    assert [t["id"] for t in data1["todos"]] == ["step-1", "step-2"]

    resp2 = await tool.run(
        tool_req,
        {
            "merge": True,
            "todos": [
                {"id": "step-2", "status": "completed"},
                {"id": "step-3", "title": "Third", "status": "pending"},
            ],
        },
    )
    data2 = json.loads(resp2.text or "{}")
    ids2 = [t["id"] for t in data2["todos"]]
    assert ids2 == ["step-2", "step-3", "step-1"]
    statuses2 = {t["id"]: t["status"] for t in data2["todos"]}
    assert statuses2["step-2"] == "completed"
    assert statuses2["step-3"] == "pending"


@pytest.mark.asyncio
async def test_update_plan_tool_enforces_single_in_progress() -> None:
    project = StubProject()
    ToolClass = ToolFactory.get("update_plan")
    assert ToolClass is not None
    tool = ToolClass(project)  # type: ignore[call-arg]

    execution = vocode_state.WorkflowExecution(workflow_name="test")
    tool_req = tools_base.ToolReq(
        execution=execution, spec=ToolSpec(name="update_plan")
    )

    with pytest.raises(ValueError):
        await tool.run(
            tool_req,
            {
                "merge": False,
                "todos": [
                    {"id": "step-1", "title": "First", "status": "in_progress"},
                    {"id": "step-2", "title": "Second", "status": "in_progress"},
                ],
            },
        )
