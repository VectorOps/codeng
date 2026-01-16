from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field


class ToolCallProviderStateDTO(BaseModel):
    provider_state: Optional[Dict[str, Any]] = None


class ToolCallReqDTO(BaseModel):
    id: str
    type: str = "function"
    name: str
    arguments: Dict[str, Any]
    tool_spec: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    auto_approved: Optional[bool] = None
    created_at: datetime
    handled_at: Optional[datetime] = None
    state: Optional[ToolCallProviderStateDTO] = None


class ToolCallRespDTO(BaseModel):
    id: str
    status: str
    name: str
    result: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    created_at: datetime


class MessageDTO(BaseModel):
    id: UUID
    role: str
    text: str
    tool_call_requests: List[ToolCallReqDTO] = Field(default_factory=list)
    tool_call_responses: List[ToolCallRespDTO] = Field(default_factory=list)
    created_at: datetime


class NodeExecutionDTO(BaseModel):
    id: UUID
    node: str
    previous_id: Optional[UUID] = None
    input_messages: List[MessageDTO] = Field(default_factory=list)
    status: str
    state: Optional[Dict[str, Any]] = None
    created_at: datetime


class StepDTO(BaseModel):
    id: UUID
    execution_id: UUID
    type: str
    message: Optional[MessageDTO] = None
    output_mode: str
    outcome_name: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    llm_usage: Optional[Dict[str, Any]] = None
    is_complete: bool
    is_final: bool
    created_at: datetime


class WorkflowExecutionDTO(BaseModel):
    schema_version: int = 1
    id: UUID
    workflow_name: str
    node_executions: Dict[UUID, NodeExecutionDTO] = Field(default_factory=dict)
    steps: List[StepDTO] = Field(default_factory=list)
    llm_usage: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
