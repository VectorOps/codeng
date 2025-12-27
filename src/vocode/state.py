from enum import Enum
from typing import List, Optional, Annotated, Dict, Any, Union
from pydantic import BaseModel, Field, StringConstraints
from pydantic import model_validator
from datetime import datetime
from .lib.date import utcnow
from uuid import UUID, uuid4
from .settings import ToolSpec  # type: ignore

from .models import Role


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


class RunStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "canceled"


class StepType(str, Enum):
    # Any message sent by the node of any type
    OUTPUT_MESSAGE = "output_message"
    # Any input message sent by the user or the coding tool
    INPUT_MESSAGE = "input_message"
    # Any sort of completion step, such as LLM finishing its output
    COMPLETION = "completion"
    # Any approval. For example, tool call that's explicitly approved will have an approval in the state.
    APPROVAL = "approval"
    # Any rejection
    REJECTION = "rejection"
    # Input request
    PROMPT = "prompt"


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

    id: Optional[str] = Field(
        default=None,
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
    created_at: datetime = Field(default_factory=utcnow)


class ToolCallResp(BaseModel):
    """
    A single tool call response.
    """

    id: Optional[str] = Field(
        default=None,
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
        default=False,
        description=(
            "True if this step represents a final, stable result "
            "rather than an intermediate update."
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
