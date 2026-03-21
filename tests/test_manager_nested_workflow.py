import asyncio

import pytest

from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.manager.base import BaseManager
from vocode.persistence import state_manager as persistence_state_manager
from vocode.project import ProjectState
from vocode.runner import proto as runner_proto
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput
from vocode.runner.proto import RunEventResp, RunEventResponseType
from vocode.settings import Settings, ToolSpec, WorkflowConfig
from vocode.tools import base as tools_base


class NestedWorkflowTestProject:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools: dict[str, tools_base.BaseTool] = {}
        self.current_workflow: str | None = None
        self.history = HistoryManager()
        self.state_manager = persistence_state_manager.NullWorkflowStateManager()
        self.project_state = ProjectState()

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
        req,
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
        history = self.project.history
        has_tool_result = False
        for existing_step in inp.execution.iter_steps():
            if (
                existing_step.message is not None
                and existing_step.message.tool_call_responses
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
        history.upsert_message(inp.run, msg)
        step = history.upsert_step(
            inp.run,
            state.Step(
                workflow_execution=inp.run,
                execution_id=inp.execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
                message_id=msg.id,
                is_complete=True,
            ),
        )
        yield step


@ExecutorFactory.register("child-echo-initial")
class ChildEchoInitialExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput):
        history = self.project.history
        text = ""
        if inp.execution.input_messages:
            last = inp.execution.input_messages[-1]
            if last.text is not None:
                text = last.text
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text=f"child-final:{text}",
        )
        history.upsert_message(inp.run, msg)
        step = history.upsert_step(
            inp.run,
            state.Step(
                workflow_execution=inp.run,
                execution_id=inp.execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
                message_id=msg.id,
                is_complete=True,
            ),
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
    tools_base.unregister_tool(NestedWorkflowTool.name)
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
    tool_response_steps = [
        s
        for s in parent_exec.iter_steps()
        if s.message is not None and s.message.tool_call_responses
    ]
    assert tool_response_steps
    last_tool_step = tool_response_steps[-1]
    assert last_tool_step.message is not None
    assert last_tool_step.message.tool_call_responses
    tool_resp = last_tool_step.message.tool_call_responses[0]
    assert tool_resp.result is not None
    assert tool_resp.result.get("agent_name") == "child"
    assert tool_resp.result.get("response") == child_runner.last_final_message.text
    assert any(e.kind == runner_proto.RunEventReqKind.START_WORKFLOW for e in events)


@ExecutorFactory.register("child-crash")
class ChildCrashExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput):
        raise RuntimeError("child boom")


@pytest.mark.asyncio
async def test_nested_workflow_failure_is_returned_as_tool_error():
    models.Node._registry["tool-start-nested-workflow"] = models.Node
    models.Node._registry["child-crash"] = models.Node

    parent_node_cfg: dict[str, object] = {
        "name": "parent-node",
        "type": "tool-start-nested-workflow",
        "outcomes": [],
        "confirmation": models.Confirmation.AUTO,
    }
    child_node_cfg: dict[str, object] = {
        "name": "child-node",
        "type": "child-crash",
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
    tools_base.unregister_tool(NestedWorkflowTool.name)
    tools_base.register_tool(NestedWorkflowTool.name, NestedWorkflowTool)
    project.tools[NestedWorkflowTool.name] = NestedWorkflowTool(project)

    async def listener(*_):
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,
        run_event_listener=listener,
    )
    await manager.start()
    parent_runner = await manager.start_workflow("parent")
    assert manager._driver_task is not None
    await asyncio.wait_for(manager._driver_task, timeout=5.0)
    await manager.stop()

    node_execs_by_name: dict[str, state.NodeExecution] = {}
    for ne in parent_runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne
    parent_exec = node_execs_by_name["parent-node"]
    tool_response_steps = [
        s
        for s in parent_exec.iter_steps()
        if s.message is not None and s.message.tool_call_responses
    ]
    assert tool_response_steps
    tool_resp = tool_response_steps[-1].message.tool_call_responses[0]  # type: ignore[union-attr]
    assert tool_resp.status == state.ToolCallStatus.FAILED
    assert tool_resp.result is not None
    assert tool_resp.result.get("error") is not None
    assert "subagent" in str(tool_resp.result.get("error")).lower()
