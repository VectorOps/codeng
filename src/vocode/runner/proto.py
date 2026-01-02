from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from vocode import state


class RunEventResponseType(str, Enum):
    # No action, ignore.
    NOOP = "noop"
    # Approve
    APPROVE = "approve"
    # Decline
    DECLINE = "decline"
    # Message
    MESSAGE = "message"


class RunEventReqKind(str, Enum):
    STEP = "step"
    STATUS = "status"


class RunStats(BaseModel):
    status: state.RunnerStatus = Field(..., description="Runner status")
    current_node_name: Optional[str] = Field(
        default=None,
        description="Current node name, if any",
    )


class RunEventReq(BaseModel):
    kind: RunEventReqKind = Field(..., description="Run event kind")
    execution: state.WorkflowExecution = Field(
        ..., description="Workflow execution this step belongs to"
    )
    step: Optional[state.Step] = Field(
        default=None,
        description="Step information, required for 'step' events",
    )
    stats: Optional[RunStats] = Field(
        default=None,
        description="Runner stats, required for 'status' events",
    )

    @model_validator(mode="after")
    def _validate_by_kind(self) -> "RunEventReq":
        if self.kind == RunEventReqKind.STEP and self.step is None:
            raise ValueError("step is required for 'step' run events")
        if self.kind == RunEventReqKind.STATUS and self.stats is None:
            raise ValueError("stats is required for 'status' run events")
        return self


class RunEventResp(BaseModel):
    resp_type: RunEventResponseType = Field(..., description="Reponse type")
    message: Optional[state.Message] = Field(
        default=None, description="Optional message"
    )
