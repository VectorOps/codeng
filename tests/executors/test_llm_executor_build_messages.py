from vocode import models, state
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.llm.llm import (
    LLMExecutor,
    LLMStepState,
    ToolCallProviderState,
)
from vocode.runner.executors.llm.models import LLMNode
from tests.stub_project import StubProject


def test_build_messages_with_tool_call_and_tool_result() -> None:
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = run.create_node_execution(
        node="llm-node",
        input_message_ids=[],
        step_ids=[],
        status=state.RunStatus.RUNNING,
    )

    tool_req = state.ToolCallReq(
        id="call-test-tool-req",
        name="test-tool",
        arguments={"x": 1},
        state=ToolCallProviderState(provider_state={"thought_signature": "sig-123"}),
    )
    tool_resp = state.ToolCallResp(
        id="call-1",
        name="test-tool",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    assistant_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="call tool",
        tool_call_requests=[tool_req],
        tool_call_responses=[tool_resp],
    )
    run.add_message(assistant_msg)
    run.create_step(
        execution_id=execution.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=assistant_msg.id,
        is_complete=True,
    )

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

    run = state.WorkflowExecution(workflow_name="wf")
    execution = run.create_node_execution(
        node="llm-node",
        input_message_ids=[],
        step_ids=[],
        status=state.RunStatus.RUNNING,
    )

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

    run = state.WorkflowExecution(workflow_name="wf")
    execution = run.create_node_execution(
        node="llm-node",
        input_message_ids=[],
        step_ids=[],
        status=state.RunStatus.RUNNING,
    )

    assistant_msg = state.Message(role=models.Role.ASSISTANT, text="hello")
    run.add_message(assistant_msg)
    run.create_step(
        execution_id=execution.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=assistant_msg.id,
        state=LLMStepState(provider_state={"cache_control": {"type": "ephemeral"}}),
        is_complete=True,
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    conv = executor.build_messages(inp)

    assert len(conv) == 1
    first = conv[0]
    assert first["role"] == "assistant"
    assert first["content"] == "hello"
    assert first["provider_specific_fields"] == {"cache_control": {"type": "ephemeral"}}


def test_build_step_from_message_reuses_existing_message_id_for_updates() -> None:
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = run.create_node_execution(
        node="llm-node",
        input_message_ids=[],
        step_ids=[],
        status=state.RunStatus.RUNNING,
    )
    base_step = run.create_step(
        execution_id=execution.id,
        type=state.StepType.OUTPUT_MESSAGE,
    )

    executor = LLMExecutor(config=cfg, project=StubProject())

    interim_step = executor._build_step_from_message(
        base_step,
        role=models.Role.ASSISTANT,
        step_type=state.StepType.OUTPUT_MESSAGE,
        text="first",
    )
    final_step = executor._build_step_from_message(
        interim_step,
        role=models.Role.ASSISTANT,
        step_type=state.StepType.OUTPUT_MESSAGE,
        text="second",
        is_complete=True,
    )

    assert interim_step.message_id is not None
    assert final_step.message_id == interim_step.message_id
    assert final_step.message is not None
    assert final_step.message.text == "second"
    assert len(run.messages_by_id) == 1
