from typing import Optional
from pydantic import BaseModel, Field
from vocode import state
from enum import Enum


class RunEventResponseType(str, Enum):
    # No action, ignore.
    NOOP = "noop"
    # Approve
    APPROVE = "approve"
    # Decline
    DECLINE = "decline"
    # Message
    MESSAGE = "message"


class RunEventReq(BaseModel):
    execution: state.WorkflowExecution = Field(
        ..., description="Workflow execution this step belongs to"
    )
    step: state.Step = Field(..., description="Step information")


class RunEventResp(BaseModel):
    resp_type: RunEventResponseType = Field(..., description="Reponse type")
    message: Optional[state.Message] = Field(
        default=None, description="Optional message"
    )
