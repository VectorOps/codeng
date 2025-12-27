from typing import Optional, List

from vocode import state, models


def concatenate_messages(
    messages: List[state.Message],
    tool_message: Optional[state.Message] = None,
    default_role: models.Role = models.Role.USER,
) -> Optional[state.Message]:
    if not messages and tool_message is None:
        return None

    text_parts: List[str] = []
    for m in messages:
        if m.text:
            text_parts.append(m.text)

    combined_text = "\n\n".join(text_parts)

    if tool_message is not None:
        role = tool_message.role
        tool_call_requests = list(tool_message.tool_call_requests)
        tool_call_responses = list(tool_message.tool_call_responses)
    elif messages:
        role = messages[-1].role
        tool_call_requests = []
        tool_call_responses = []
    else:
        role = default_role
        tool_call_requests = []
        tool_call_responses = []

    return state.Message(
        role=role,
        text=combined_text,
        tool_call_requests=tool_call_requests,
        tool_call_responses=tool_call_responses,
    )