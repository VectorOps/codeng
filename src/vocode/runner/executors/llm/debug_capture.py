from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import connect


def should_capture_debug_prompt_response(project_settings: Any) -> bool:
    if project_settings is None or project_settings.debugging is None:
        return False
    return bool(project_settings.debugging.capture_llm_payload)


def serialize_connect_message_for_debug(
    message: connect.Message,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "role": message.role,
    }
    content = getattr(message, "content", None)
    if isinstance(content, str):
        payload["content"] = content
    elif isinstance(content, list):
        blocks: List[Dict[str, Any]] = []
        for block in content:
            block_payload: Dict[str, Any] = {
                "type": block.type,
            }
            if block.type == "text":
                block_payload["text"] = block.text
            elif block.type == "tool_call":
                block_payload["id"] = block.id
                block_payload["name"] = block.name
                block_payload["arguments"] = dict(block.arguments or {})
                if block.provider_meta:
                    block_payload["provider_meta"] = dict(block.provider_meta)
                if block.protocol_meta:
                    block_payload["protocol_meta"] = dict(block.protocol_meta)
                if block.annotations:
                    block_payload["annotations"] = dict(block.annotations)
            blocks.append(block_payload)
        payload["content"] = blocks
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    tool_name = getattr(message, "tool_name", None)
    if tool_name is not None:
        payload["tool_name"] = tool_name
    provider_meta = getattr(message, "provider_meta", None)
    if provider_meta:
        payload["provider_meta"] = dict(provider_meta)
    protocol_meta = getattr(message, "protocol_meta", None)
    if protocol_meta:
        payload["protocol_meta"] = dict(protocol_meta)
    return payload


def build_debug_request_payload(
    request: connect.GenerateRequest,
    system_prompt: Optional[str],
    connect_messages: List[connect.Message],
    tools: Optional[List[connect.ToolSpec]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "system_prompt": system_prompt,
        "messages": [
            serialize_connect_message_for_debug(message) for message in connect_messages
        ],
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
    }
    if request.reasoning is not None:
        payload["reasoning"] = {
            "effort": request.reasoning.effort,
        }
    if tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]
    return payload


def build_debug_response_payload(
    response: connect.AssistantMessage,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "provider": response.provider,
        "model": response.model,
        "api_family": response.api_family,
        "finish_reason": response.finish_reason,
        "response_id": response.response_id,
        "request_id": response.request_id,
        "content": [
            serialize_connect_message_for_debug(
                connect.AssistantMessage(content=[block])
            )["content"][0]
            for block in response.content
        ],
    }
    if response.usage is not None:
        payload["usage"] = response.usage.model_dump(mode="json")
    if response.protocol_state:
        payload["protocol_state"] = dict(response.protocol_state)
    if response.provider_meta:
        payload["provider_meta"] = dict(response.provider_meta)
    return payload


def build_step_debug_payload(
    request_payload: Optional[Dict[str, Any]],
    response: connect.AssistantMessage,
) -> Dict[str, Any]:
    return {
        "request": request_payload,
        "response": build_debug_response_payload(response),
    }


def build_debug_text_payload(
    request_payload: Optional[Dict[str, Any]],
    response: connect.AssistantMessage,
) -> str:
    return json.dumps(
        build_step_debug_payload(request_payload, response),
        ensure_ascii=False,
        sort_keys=True,
    )
