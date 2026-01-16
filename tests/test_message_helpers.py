from vocode.lib import message_helpers
from vocode import state, models


def test_concatenate_messages_combines_input_and_final() -> None:
    input_msg = state.Message(role=models.Role.USER, text="input")
    final_msg = state.Message(role=models.Role.ASSISTANT, text="final")

    combined = message_helpers.concatenate_messages(
        [input_msg, final_msg],
        tool_message=final_msg,
    )

    assert combined is not None
    assert combined.text == "input\n\nfinal"
    assert combined.role == models.Role.ASSISTANT


def test_concatenate_messages_uses_last_input_role_when_no_final() -> None:
    msg1 = state.Message(
        role=models.Role.USER,
        text="first",
    )
    msg2 = state.Message(
        role=models.Role.ASSISTANT,
        text="second",
    )

    combined = message_helpers.concatenate_messages([msg1, msg2])

    assert combined is not None
    assert combined.text == "first\n\nsecond"
    assert combined.role == models.Role.ASSISTANT


def test_concatenate_messages_returns_none_when_no_messages() -> None:
    combined = message_helpers.concatenate_messages([])

    assert combined is None


def test_concatenate_messages_copies_tool_calls_from_final_only() -> None:
    input_msg = state.Message(role=models.Role.USER, text="input")
    tool_req = state.ToolCallReq(
        id="call-t-req",
        name="t",
        arguments={},
        state=state.ToolCallProviderState(
            provider_state={"thought_signature": "sig-xyz"}
        ),
    )
    tool_resp = state.ToolCallResp(
        id="call-t",
        name="t",
        result={"ok": True},
    )
    final_msg = state.Message(
        role=models.Role.TOOL,
        text="final",
        tool_call_requests=[tool_req],
        tool_call_responses=[tool_resp],
    )

    combined = message_helpers.concatenate_messages(
        [input_msg, final_msg],
        tool_message=final_msg,
    )

    assert combined is not None
    assert combined.tool_call_requests == final_msg.tool_call_requests
    assert combined.tool_call_responses == final_msg.tool_call_responses
    assert combined.tool_call_requests[0].state is not None
    assert combined.tool_call_requests[0].state.provider_state == {
        "thought_signature": "sig-xyz"
    }