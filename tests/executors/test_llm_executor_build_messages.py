from vocode import state, models
from tests.stub_project import StubProject
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.llm.llm import LLMExecutor, ToolCallProviderState, LLMStepState
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
        state=ToolCallProviderState(provider_state={"thought_signature": "sig-123"}),
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
    assert first["tool_calls"][0]["provider_specific_fields"] == {
        "thought_signature": "sig-123"
    }

    second = conv[1]
    assert second["role"] == "tool"
    assert second["tool_call_id"] == "call-1"
    assert second["name"] == "test-tool"
    assert second["content"] == '{"ok": true}'
    assert "arguments" not in second


def test_build_messages_applies_preprocessors_to_system_prompt() -> None:
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
        system="base system",
        preprocessors=[
            models.PreprocessorSpec(
                name="string_inject",
                options={"text": "prefix", "separator": "\n--\n"},
                mode=models.Role.SYSTEM,
                prepend=True,
            )
        ],
    )

    execution = state.NodeExecution(
        node="llm-node",
        input_messages=[],
        steps=[],
        status=state.RunStatus.RUNNING,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    run.node_executions[execution.id] = execution

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    conv = executor.build_messages(inp)

    assert len(conv) == 1
    first = conv[0]
    assert first["role"] == "system"
    assert first["content"].startswith("prefix")
    assert "base system" in first["content"]
    assert "\n--\n" in first["content"]


def test_build_messages_copies_llm_step_state_provider_fields_to_message() -> None:
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

    assistant_msg = state.Message(role=models.Role.ASSISTANT, text="hello")
    assistant_step = state.Step(
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=assistant_msg,
        state=LLMStepState(provider_state={"cache_control": {"type": "ephemeral"}}),
        is_complete=True,
    )
    execution.steps.append(assistant_step)
    run.steps.append(assistant_step)

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    conv = executor.build_messages(inp)

    assert len(conv) == 1
    first = conv[0]
    assert first["role"] == "assistant"
    assert first["content"] == "hello"
    assert first["provider_specific_fields"] == {"cache_control": {"type": "ephemeral"}}
