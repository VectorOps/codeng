from enum import Enum
from typing import List, Optional, Annotated, Dict, Any, Union
from pydantic import BaseModel, Field, StringConstraints
from pydantic import model_validator
from datetime import datetime
from .lib.date import utcnow
from uuid import UUID, uuid4
from .settings import ToolSpec  # type: ignore
from .models import OutputMode, Role


class OpaqueState(BaseModel):
    model: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class RunnerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    STOPPED = "canceled"
    FINISHED = "finished"


class ToolCallStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ToolCallReqStatus(str, Enum):
    REQUIRES_CONFIRMATION = "requires_confirmation"
    PENDING_EXECUTION = "pending_execution"
    EXECUTING = "executing"
    REJECTED = "rejected"
    COMPLETE = "complete"


class RunStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "canceled"


class StepType(str, Enum):
    OUTPUT_MESSAGE = "output_message"
    INPUT_MESSAGE = "input_message"
    APPROVAL = "approval"
    REJECTION = "rejection"
    PROMPT = "prompt"
    PROMPT_CONFIRM = "prompt_confirm"
    TOOL_REQUEST = "tool_request"
    TOOL_RESULT = "tool_result"
    WORKFLOW_REQUEST = "workflow_request"
    WORKFLOW_RESULT = "workflow_result"


class LLMUsageStats(BaseModel):
    """
    Aggregated LLM usage and limits.
    Used for global, per-session, and per-node accounting.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_dollars: float = 0.0
    # Model limits; typically represent input (context window) and optional output cap
    input_token_limit: Optional[int] = None
    output_token_limit: Optional[int] = None


class ToolCallReq(BaseModel):
    """
    A single tool call request.
    """

    id: str = Field(
        ...,
        description="Provider-issued id for this tool call (e.g., 'call_...')",
    )
    type: str = Field(
        default="function",
        description="Tool call type (currently 'function' per OpenAI schema)",
    )
    name: str = Field(..., description="Function name to call")
    arguments: Dict[str, Any] = Field(
        ..., description="Decoded JSON arguments passed to the function"
    )
    tool_spec: Optional[ToolSpec] = Field(
        default=None,
        description="Effective ToolSpec used for this call, if any.",
    )
    status: Optional[ToolCallReqStatus] = Field(
        default=None,
        description="Execution status for this tool call request.",
    )
    auto_approved: Optional[bool] = Field(
        default=None,
        description="Set to a truthy value at runtime when this tool call is auto-approved.",
    )
    created_at: datetime = Field(default_factory=utcnow)
    handled_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when this tool call was handled, if applicable.",
    )
    state: Optional[BaseModel] = Field(
        default=None,
        description="Provider-specific state to preserve across turns (e.g. thought signatures).",
    )


class ToolCallResp(BaseModel):
    """
    A single tool call response.
    """

    id: str = Field(
        ...,
        description="Provider-issued id for this tool call (e.g., 'call_...')",
    )
    status: ToolCallStatus = Field(
        default=ToolCallStatus.CREATED, description="Tool call status"
    )
    name: str = Field(..., description="Function name to call")
    result: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = Field(
        default=None,
        description="Decoded JSON result of the function call; may be a dict or a list of dicts; None until completed",
    )
    created_at: datetime = Field(default_factory=utcnow)


class Message(BaseModel):
    """
    A single human-readable message that's produced by a human or a tool.
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this message"
    )
    role: Role = Field(..., description="Sender role")
    text: str = Field(..., description="Original message as received/emitted")
    thinking_content: Optional[str] = Field(
        default=None,
        description="Optional model reasoning/thinking content (not shown as user-visible text).",
    )

    tool_call_requests: List[ToolCallReq] = Field(
        default_factory=list,
        description="Tool call requests",
    )
    tool_call_responses: List[ToolCallResp] = Field(
        default_factory=list,
        description="Tool call responses",
    )
    created_at: datetime = Field(default_factory=utcnow)


class NodeExecution(BaseModel):
    """
    A single node execution
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this node execution"
    )
    node: str = Field(..., description="Node name this execution pertains to")
    previous: Optional["NodeExecution"] = Field(
        default=None,
        description="Previous execution for the same node, if any.",
    )
    input_messages: List[Message] = Field(
        default_factory=list,
        description="Initial input messages for this node",
    )
    steps: List["Step"] = Field(default_factory=list, description="A list of steps")
    status: RunStatus = Field(..., description="Node execution status")
    state: Optional[BaseModel] = Field(
        default=None,
        description="Any internal state that is maintained by the corresponding step runner.",
    )
    created_at: datetime = Field(default_factory=utcnow)


class Step(BaseModel):
    """
    This class epresents a single execution step in the workflow. Any LLM
    message, any user input, etc will generate a step.
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this step"
    )
    execution: NodeExecution = Field(..., description="Node execution for this step")
    type: StepType = Field(..., description="Step Type")
    message: Optional[Message] = Field(
        default=None, description="Message carried by this step, if any."
    )
    output_mode: OutputMode = Field(
        default=OutputMode.SHOW,
        description="How this step's output is presented in the UI.",
    )
    outcome_name: Optional[str] = Field(
        default=None, description="Outcome name, if any."
    )
    state: Optional[BaseModel] = Field(
        default=None,
        description="Any internal state that is maintained by the corresponding step runner.",
    )
    llm_usage: Optional[LLMUsageStats] = Field(
        default=None, description="LLM usage stats for this step, if any."
    )
    is_complete: bool = Field(
        default=True,
        description=(
            "True if this step represents a final, stable result "
            "rather than an intermediate update."
        ),
    )
    is_final: bool = Field(
        default=False,
        description=(
            "True if this is the step that triggered transition to the next node. "
            "At most one step per node execution is final at any time, and usually the last."
        ),
    )
    created_at: datetime = Field(default_factory=utcnow)


class WorkflowExecution(BaseModel):
    """
    A single workflow execution.
    """

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this workflow execution",
    )
    workflow_name: str = Field()
    node_executions: Dict[UUID, NodeExecution] = Field(default_factory=dict)
    steps: List[Step] = Field(default_factory=list)
    llm_usage: Optional[LLMUsageStats] = Field(
        default=None, description="LLM usage stats for this step, if any."
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def delete_step(self, step_id: UUID) -> None:
        self.delete_steps([step_id])

    def delete_steps(self, step_ids: List[UUID]) -> None:
        """
        Delete one or more steps from the execution state.
        """
        if not step_ids:
            return
        step_ids_set = set(step_ids)
        executions_step_ids: Dict[UUID, List[UUID]] = {}
        remaining_steps: List[Step] = []
        for step in self.steps:
            if step.id in step_ids_set:
                execution_id = step.execution.id
                if execution_id in self.node_executions:
                    if execution_id not in executions_step_ids:
                        executions_step_ids[execution_id] = []
                    executions_step_ids[execution_id].append(step.id)
            else:
                remaining_steps.append(step)
        self.steps = remaining_steps
        for execution_id, removed_ids in executions_step_ids.items():
            execution = self.node_executions.get(execution_id)
            if execution is not None:
                removed_set = set(removed_ids)
                execution.steps = [
                    step for step in execution.steps if step.id not in removed_set
                ]

    def delete_node_execution(self, execution_id: UUID) -> None:
        execution = self.node_executions.get(execution_id)
        if execution is None:
            return
        step_ids = [step.id for step in execution.steps]
        if step_ids:
            self.delete_steps(step_ids)
        if execution_id in self.node_executions:
            del self.node_executions[execution_id]

    def trim_empty_node_executions(self) -> None:
        empty_execution_ids: List[UUID] = []
        for execution_id, execution in self.node_executions.items():
            if not execution.steps:
                empty_execution_ids.append(execution_id)
        for execution_id in empty_execution_ids:
            self.delete_node_execution(execution_id)
