from enum import Enum
from typing import Optional, Annotated, Union

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
    START_WORKFLOW = "start_workflow"


class ToolExecResultKind(str, Enum):
    RESPONSE = "response"
    START_WORKFLOW = "start_workflow"


class ToolExecResponse(BaseModel):
    kind: ToolExecResultKind = Field(default=ToolExecResultKind.RESPONSE)
    response: state.ToolCallResp


class ToolExecStartWorkflow(BaseModel):
    kind: ToolExecResultKind = Field(default=ToolExecResultKind.START_WORKFLOW)
    workflow_name: str
    initial_text: Optional[str] = None
    initial_message: Optional[state.Message] = None


ToolExecResult = Annotated[
    Union[ToolExecResponse, ToolExecStartWorkflow],
    Field(discriminator="kind"),
]


class RunStats(BaseModel):
    status: state.RunnerStatus = Field(..., description="Runner status")
    current_node_name: Optional[str] = Field(
        default=None,
        description="Current node name, if any",
    )


class RunEventStartWorkflow(BaseModel):
    workflow_name: str
    initial_message: Optional[state.Message] = None


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
    start_workflow: Optional[RunEventStartWorkflow] = Field(
        default=None,
        description="Start-workflow payload, required for 'start_workflow' events",
    )

    @model_validator(mode="after")
    def _validate_by_kind(self) -> "RunEventReq":
        if self.kind == RunEventReqKind.STEP:
            if self.step is None:
                raise ValueError("step is required for 'step' run events")
        elif self.kind == RunEventReqKind.STATUS:
            if self.stats is None:
                raise ValueError("stats is required for 'status' run events")
        elif self.kind == RunEventReqKind.START_WORKFLOW:
            if self.start_workflow is None:
                raise ValueError(
                    "start_workflow is required for 'start_workflow' run events"
                )
        return self


class RunEventResp(BaseModel):
    resp_type: RunEventResponseType = Field(..., description="Reponse type")
    message: Optional[state.Message] = Field(
        default=None, description="Optional message"
    )
