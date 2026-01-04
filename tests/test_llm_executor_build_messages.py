from vocode import state, models
from tests.stub_project import StubProject
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.llm.llm import LLMExecutor
from vocode.runner.executors.llm.models import LLMNode


def test_build_messages_with_tool_call_and_tool_result() -> None:
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    execution = state.NodeExecution(
        node="llm-node",
        input_messages=[],
        steps=[],
        status=state.RunStatus.RUNNING,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    run.node_executions[execution.id] = execution

    tool_req = state.ToolCallReq(
        id="call-test-tool-req",
        name="test-tool",
        arguments={"x": 1},
    )
    assistant_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="call tool",
        tool_call_requests=[tool_req],
    )
    assistant_step = state.Step(
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=assistant_msg,
        is_complete=True,
    )
    execution.steps.append(assistant_step)
    run.steps.append(assistant_step)

    tool_resp = state.ToolCallResp(
        id="call-1",
        name="test-tool",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    tool_msg = state.Message(
        role=models.Role.TOOL,
        text="",
        tool_call_responses=[tool_resp],
    )
    tool_step = state.Step(
        execution=execution,
        type=state.StepType.TOOL_RESULT,
        message=tool_msg,
        is_complete=True,
    )
    execution.steps.append(tool_step)
    run.steps.append(tool_step)

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    conv = executor.build_messages(inp)

    assert len(conv) == 2

    first = conv[0]
    assert first["role"] == "assistant"
    assert "tool_calls" in first
    assert first["tool_calls"][0]["function"]["name"] == "test-tool"

    second = conv[1]
    assert second["role"] == "tool"
    assert second["tool_call_id"] == "call-1"
    assert second["name"] == "test-tool"
    assert second["content"] == '{"ok": true}'
    assert "arguments" not in second
