import asyncio

import pytest

from vocode import models, state
from vocode.manager.base import BaseManager
from vocode.runner import proto as runner_proto
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput
from vocode.runner.proto import RunEventResp, RunEventResponseType
from vocode.settings import Settings, WorkflowConfig, ToolSpec
from vocode.tools import base as tools_base

class NestedWorkflowTestProject:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools: dict[str, tools_base.BaseTool] = {}
        self.current_workflow: str | None = None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def add_llm_usage(
        self,
        prompt_delta: int,
        completion_delta: int,
        cost_delta: float,
    ) -> None:
        return None


class NestedWorkflowTool(tools_base.BaseTool):
    name = "nested-workflow-test-tool"

    async def run(
        self,
        spec: ToolSpec,
        args,
    ) -> tools_base.ToolResponse | None:
        text = ""
        if isinstance(args, dict):
            value = args.get("text")
            if isinstance(value, str):
                text = value
        return tools_base.ToolStartWorkflowResponse(
            workflow="child",
            initial_text=text,
        )

    async def openapi_spec(
        self,
        spec: ToolSpec,
    ) -> dict:
        return {}


@ExecutorFactory.register("tool-start-nested-workflow")
class StartNestedWorkflowExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput):
        execution = inp.execution
        has_tool_result = False
        for existing_step in execution.steps:
            if (
                existing_step.type == state.StepType.TOOL_RESULT
                and existing_step.is_complete
            ):
                has_tool_result = True
                break
        if not has_tool_result:
            tool_req = state.ToolCallReq(
                id="call-nested",
                name="nested-workflow-test-tool",
                arguments={"text": "parent-input"},
            )
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="before-nested",
                tool_call_requests=[tool_req],
            )
        else:
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="after-nested",
            )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


@ExecutorFactory.register("child-echo-initial")
class ChildEchoInitialExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput):
        execution = inp.execution
        text = ""
        if execution.input_messages:
            last = execution.input_messages[-1]
            if last.text is not None:
                text = last.text
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text=f"child-final:{text}",
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


@pytest.mark.asyncio
async def test_nested_workflow_execution_via_manager():
    models.Node._registry["tool-start-nested-workflow"] = models.Node
    models.Node._registry["child-echo-initial"] = models.Node

    parent_node_cfg: dict[str, object] = {
        "name": "parent-node",
        "type": "tool-start-nested-workflow",
        "outcomes": [],
        "confirmation": models.Confirmation.AUTO,
    }
    child_node_cfg: dict[str, object] = {
        "name": "child-node",
        "type": "child-echo-initial",
        "outcomes": [],
        "confirmation": models.Confirmation.AUTO,
    }
    settings = Settings(
        workflows={
            "parent": WorkflowConfig(
                name="parent",
                need_input=False,
                nodes=[parent_node_cfg],
                edges=[],
            ),
            "child": WorkflowConfig(
                name="child",
                need_input=False,
                nodes=[child_node_cfg],
                edges=[],
            ),
        },
        tools=[ToolSpec(name="nested-workflow-test-tool", auto_approve=True)],
    )
    project = NestedWorkflowTestProject(settings=settings)
    tools_base.register_tool(NestedWorkflowTool.name, NestedWorkflowTool)
    project.tools[NestedWorkflowTool.name] = NestedWorkflowTool(project)
    events: list[runner_proto.RunEventReq] = []
    child_runner = None

    async def listener(frame, event):
        nonlocal child_runner
        events.append(event)
        if event.kind == runner_proto.RunEventReqKind.START_WORKFLOW:
            assert event.start_workflow is not None
            child_runner = await manager.start_workflow(
                event.start_workflow.workflow_name,
                initial_message=event.start_workflow.initial_message,
            )
            return None
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(project=project, run_event_listener=listener)
    await manager.start()
    parent_runner = await manager.start_workflow("parent")
    assert manager._driver_task is not None
    await asyncio.wait_for(manager._driver_task, timeout=5.0)
    await manager.stop()
    assert child_runner is not None
    assert child_runner.last_final_message is not None
    assert child_runner.last_final_message.text == "child-final:parent-input"
    assert parent_runner.status == state.RunnerStatus.FINISHED
    node_execs_by_name: dict[str, state.NodeExecution] = {}
    for ne in parent_runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne
    assert "parent-node" in node_execs_by_name
    parent_exec = node_execs_by_name["parent-node"]
    tool_result_steps = [
        s for s in parent_exec.steps if s.type == state.StepType.TOOL_RESULT
    ]
    assert tool_result_steps
    last_tool_step = tool_result_steps[-1]
    assert last_tool_step.message is not None
    assert last_tool_step.message.tool_call_responses
    tool_resp = last_tool_step.message.tool_call_responses[0]
    assert tool_resp.result is not None
    assert tool_resp.result.get("agent_name") == "child"
    assert (
        tool_resp.result.get("response")
        == child_runner.last_final_message.text
    )
    assert any(
        e.kind == runner_proto.RunEventReqKind.START_WORKFLOW for e in events
    )
