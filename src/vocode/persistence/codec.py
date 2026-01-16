from __future__ import annotations

import gzip
import json
import enum
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

from vocode import state
from vocode.persistence import dto as persistence_dto


def _dump_opaque_state(value: Optional[state.BaseModel]) -> Optional[Dict[str, Any]]:  # type: ignore[name-defined]
    if value is None:
        return None
    payload: Dict[str, Any] = {
        "model": f"{value.__class__.__module__}.{value.__class__.__name__}",
        "data": value.model_dump(),
    }
    return payload


def _load_opaque_state(payload: Optional[Dict[str, Any]]) -> Optional[state.OpaqueState]:
    if payload is None:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    model = payload.get("model")
    if not isinstance(model, str):
        model = None
    return state.OpaqueState(model=model, data=data)

def _dump_enum(value: object) -> str:
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def to_dto(execution: state.WorkflowExecution) -> persistence_dto.WorkflowExecutionDTO:
    node_dtos: Dict[uuid.UUID, persistence_dto.NodeExecutionDTO] = {}
    for execution_id, node_exec in execution.node_executions.items():
        prev_id = node_exec.previous.id if node_exec.previous is not None else None
        node_dtos[execution_id] = persistence_dto.NodeExecutionDTO(
            id=node_exec.id,
            node=node_exec.node,
            previous_id=prev_id,
            input_messages=[
                persistence_dto.MessageDTO(
                    id=m.id,
                    role=_dump_enum(m.role),
                    text=m.text,
                    tool_call_requests=[
                        persistence_dto.ToolCallReqDTO(
                            id=req.id,
                            type=req.type,
                            name=req.name,
                            arguments=req.arguments,
                            tool_spec=(
                                req.tool_spec.model_dump()
                                if req.tool_spec is not None
                                else None
                            ),
                            status=_dump_enum(req.status) if req.status is not None else None,
                            auto_approved=req.auto_approved,
                            created_at=req.created_at,
                            handled_at=req.handled_at,
                            state=(
                                persistence_dto.ToolCallProviderStateDTO(
                                    provider_state=req.state.provider_state
                                    if req.state is not None
                                    else None
                                )
                                if req.state is not None
                                else None
                            ),
                        )
                        for req in m.tool_call_requests
                    ],
                    tool_call_responses=[
                        persistence_dto.ToolCallRespDTO(
                            id=resp.id,
                            status=_dump_enum(resp.status),
                            name=resp.name,
                            result=resp.result,
                            created_at=resp.created_at,
                        )
                        for resp in m.tool_call_responses
                    ],
                    created_at=m.created_at,
                )
                for m in node_exec.input_messages
            ],
            status=_dump_enum(node_exec.status),
            state=_dump_opaque_state(node_exec.state),
            created_at=node_exec.created_at,
        )

    step_dtos: list[persistence_dto.StepDTO] = []
    for s in execution.steps:
        msg = s.message
        msg_dto: persistence_dto.MessageDTO | None = None
        if msg is not None:
            msg_dto = persistence_dto.MessageDTO(
                id=msg.id,
                role=_dump_enum(msg.role),
                text=msg.text,
                tool_call_requests=[
                    persistence_dto.ToolCallReqDTO(
                        id=req.id,
                        type=req.type,
                        name=req.name,
                        arguments=req.arguments,
                        tool_spec=(
                            req.tool_spec.model_dump() if req.tool_spec is not None else None
                        ),
                        status=_dump_enum(req.status) if req.status is not None else None,
                        auto_approved=req.auto_approved,
                        created_at=req.created_at,
                        handled_at=req.handled_at,
                        state=(
                            persistence_dto.ToolCallProviderStateDTO(
                                provider_state=req.state.provider_state
                                if req.state is not None
                                else None
                            )
                            if req.state is not None
                            else None
                        ),
                    )
                    for req in msg.tool_call_requests
                ],
                tool_call_responses=[
                    persistence_dto.ToolCallRespDTO(
                        id=resp.id,
                        status=_dump_enum(resp.status),
                        name=resp.name,
                        result=resp.result,
                        created_at=resp.created_at,
                    )
                    for resp in msg.tool_call_responses
                ],
                created_at=msg.created_at,
            )

        step_dtos.append(
            persistence_dto.StepDTO(
                id=s.id,
                execution_id=s.execution.id,
                type=_dump_enum(s.type),
                message=msg_dto,
                output_mode=_dump_enum(s.output_mode),
                outcome_name=s.outcome_name,
                state=_dump_opaque_state(s.state),
                llm_usage=s.llm_usage.model_dump() if s.llm_usage is not None else None,
                is_complete=s.is_complete,
                is_final=s.is_final,
                created_at=s.created_at,
            )
        )

    return persistence_dto.WorkflowExecutionDTO(
        id=execution.id,
        workflow_name=execution.workflow_name,
        node_executions=node_dtos,
        steps=step_dtos,
        llm_usage=execution.llm_usage.model_dump() if execution.llm_usage is not None else None,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


def from_dto(dto: persistence_dto.WorkflowExecutionDTO) -> state.WorkflowExecution:
    run = state.WorkflowExecution(
        id=dto.id,
        workflow_name=dto.workflow_name,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        llm_usage=(
            state.LLMUsageStats.model_validate(dto.llm_usage)
            if dto.llm_usage is not None
            else None
        ),
    )

    node_execs: dict[uuid.UUID, state.NodeExecution] = {}
    previous_links: dict[uuid.UUID, Optional[uuid.UUID]] = {}
    for execution_id, node_dto in dto.node_executions.items():
        node_execs[execution_id] = state.NodeExecution(
            id=node_dto.id,
            node=node_dto.node,
            previous=None,
            input_messages=[
                state.Message.model_validate(m.model_dump())
                for m in node_dto.input_messages
            ],
            steps=[],
            status=state.RunStatus(node_dto.status),
            state=_load_opaque_state(node_dto.state),
            created_at=node_dto.created_at,
        )
        previous_links[execution_id] = node_dto.previous_id

    for execution_id, prev_id in previous_links.items():
        if prev_id is None:
            continue
        current = node_execs.get(execution_id)
        prev = node_execs.get(prev_id)
        if current is not None:
            current.previous = prev

    run.node_executions = node_execs

    steps: list[state.Step] = []
    for step_dto in dto.steps:
        node_exec = run.node_executions.get(step_dto.execution_id)
        if node_exec is None:
            continue
        step = state.Step(
            id=step_dto.id,
            execution=node_exec,
            type=state.StepType(step_dto.type),
            message=(
                state.Message.model_validate(step_dto.message.model_dump())
                if step_dto.message is not None
                else None
            ),
            output_mode=state.OutputMode(step_dto.output_mode),
            outcome_name=step_dto.outcome_name,
            state=_load_opaque_state(step_dto.state),
            llm_usage=(
                state.LLMUsageStats.model_validate(step_dto.llm_usage)
                if step_dto.llm_usage is not None
                else None
            ),
            is_complete=step_dto.is_complete,
            is_final=step_dto.is_final,
            created_at=step_dto.created_at,
        )
        node_exec.steps.append(step)
        steps.append(step)

    run.steps = steps
    return run


def dumps_gzip(execution: state.WorkflowExecution) -> bytes:
    dto = to_dto(execution)
    raw = dto.model_dump_json().encode("utf-8")
    return gzip.compress(raw)


def loads_gzip(data: bytes) -> state.WorkflowExecution:
    raw = gzip.decompress(data).decode("utf-8")
    obj = json.loads(raw)
    dto = persistence_dto.WorkflowExecutionDTO.model_validate(obj)
    return from_dto(dto)


def save_to_path(path: Path, execution: state.WorkflowExecution) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps_gzip(execution)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def load_from_path(path: Path) -> state.WorkflowExecution:
    data = path.read_bytes()
    return loads_gzip(data)
