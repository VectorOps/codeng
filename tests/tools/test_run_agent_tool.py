import pytest
from vocode.tools.run_agent import RunAgentTool
from vocode.settings import ToolSpec
from vocode.state import WorkflowExecution, Message
from vocode.tools.base import ToolReq, ToolStartWorkflowResponse, ToolResponseType
from tests.stub_project import StubProject

@pytest.mark.asyncio
async def test_run_agent_tool_success() -> None:
    project = StubProject()
    tool = RunAgentTool(project)
    
    spec = ToolSpec(name="run_agent", config={})
    execution = WorkflowExecution(workflow_name="test")
    req = ToolReq(execution=execution, spec=spec)
    
    args = {"name": "sub_agent", "text": "do something"}
    response = await tool.run(req, args)
    
    assert isinstance(response, ToolStartWorkflowResponse)
    assert response.type == ToolResponseType.start_workflow
    assert response.workflow == "sub_agent"
    assert response.initial_text == "do something"

@pytest.mark.asyncio
async def test_run_agent_tool_missing_args() -> None:
    project = StubProject()
    tool = RunAgentTool(project)
    
    spec = ToolSpec(name="run_agent", config={})
    execution = WorkflowExecution(workflow_name="test")
    req = ToolReq(execution=execution, spec=spec)
    
    # Missing name
    with pytest.raises(ValueError, match="requires 'name' argument"):
        await tool.run(req, {"text": "hi"})

    # Not a dict
    with pytest.raises(TypeError, match="requires dict args"):
        await tool.run(req, "some string")

@pytest.mark.asyncio
async def test_run_agent_tool_openapi_spec() -> None:
    project = StubProject()
    tool = RunAgentTool(project)
    spec = ToolSpec(name="run_agent", config={})
    
    openapi = await tool.openapi_spec(spec)
    assert openapi["name"] == "run_agent"
    assert "parameters" in openapi
    assert "name" in openapi["parameters"]["properties"]
    assert "text" in openapi["parameters"]["properties"]