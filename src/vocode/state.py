from enum import Enum
from typing import Any, Dict, List, Optional, Union

from datetime import datetime
from pydantic import BaseModel, Field, PrivateAttr, model_validator
from uuid import UUID, uuid4

from .lib.date import utcnow
from .models import OutputMode, Role, StepContentType
from .settings import ToolSpec  # type: ignore


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
    model_name: Optional[str] = None
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

    @property
    def tool_call_request_ids(self) -> List[str]:
        return [tool_call.id for tool_call in self.tool_call_requests]

    @property
    def tool_call_response_ids(self) -> List[str]:
        return [tool_call.id for tool_call in self.tool_call_responses]


class BranchRecord(BaseModel):
    id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this branch",
    )
    head_step_id: Optional[UUID] = Field(
        default=None,
        description="Current head step id for this branch, if any.",
    )
    base_step_id: Optional[UUID] = Field(
        default=None,
        description="Base step id where this branch diverged, if any.",
    )
    label: Optional[str] = Field(default=None, description="Optional branch label")
    created_at: datetime = Field(default_factory=utcnow)


class NodeExecution(BaseModel):
    """
    A single node execution
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this node execution"
    )
    node: str = Field(..., description="Node name this execution pertains to")
    previous_id: Optional[UUID] = Field(
        default=None,
        description="Previous execution id for the same node, if any.",
    )
    branch_id: Optional[UUID] = Field(
        default=None,
        description="Branch id this node execution belongs to.",
    )
    input_message_ids: List[UUID] = Field(
        default_factory=list,
        description="Initial input message ids for this node",
    )
    step_ids: List[UUID] = Field(
        default_factory=list,
        description="Step ids visible on the active branch.",
    )
    status: RunStatus = Field(..., description="Node execution status")
    state: Optional[BaseModel] = Field(
        default=None,
        description="Any internal state that is maintained by the corresponding step runner.",
    )
    created_at: datetime = Field(default_factory=utcnow)
    _workflow_execution: Optional["WorkflowExecution"] = PrivateAttr(default=None)

    def __init__(
        self,
        workflow_execution: Optional["WorkflowExecution"] = None,
        **data: Any,
    ) -> None:
        super().__init__(**data)
        self._workflow_execution = workflow_execution

    def model_copy(
        self, *, update: Optional[Dict[str, Any]] = None, deep: bool = False
    ) -> "NodeExecution":
        copied = super().model_copy(update=update, deep=deep)
        copied._workflow_execution = self._workflow_execution
        return copied

    @property
    def workflow_execution_id(self) -> UUID:
        return self._workflow_execution.id

    @property
    def previous(self) -> Optional["NodeExecution"]:
        if self.previous_id is None:
            return None
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        return self._workflow_execution.get_node_execution(self.previous_id)

    @property
    def input_messages(self) -> tuple[Message, ...]:
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        return tuple(
            self._workflow_execution.get_message(message_id)
            for message_id in self.input_message_ids
        )

    def iter_steps(self):
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        return self._workflow_execution.iter_node_steps(self.id)

    def iter_steps_reversed(self):
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        return self._workflow_execution.iter_node_steps_reversed(self.id)


class Step(BaseModel):
    """
    This class epresents a single execution step in the workflow. Any LLM
    message, any user input, etc will generate a step.
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this step"
    )
    execution_id: UUID = Field(..., description="Node execution id for this step")
    parent_step_id: Optional[UUID] = Field(
        default=None,
        description="Parent step id for active-path tree navigation.",
    )
    child_step_ids: List[UUID] = Field(
        default_factory=list,
        description="Child step ids for active-path tree navigation.",
    )
    type: StepType = Field(..., description="Step Type")
    message_id: Optional[UUID] = Field(
        default=None, description="Message id carried by this step, if any."
    )
    content_type: StepContentType = Field(
        default=StepContentType.MARKDOWN,
        description="How this step's message should be rendered in the UI.",
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
    status_hint: Optional[RunnerStatus] = Field(
        default=None,
        description=(
            "Optional hint to the runner about the current status while this step is being processed."
        ),
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
    _workflow_execution: Optional["WorkflowExecution"] = PrivateAttr(default=None)

    def __init__(
        self,
        workflow_execution: Optional["WorkflowExecution"] = None,
        **data: Any,
    ) -> None:
        super().__init__(**data)
        self._workflow_execution = workflow_execution

    def model_copy(
        self, *, update: Optional[Dict[str, Any]] = None, deep: bool = False
    ) -> "Step":
        copied = super().model_copy(update=update, deep=deep)
        copied._workflow_execution = self._workflow_execution
        return copied

    @property
    def workflow_execution_id(self) -> UUID:
        return self._workflow_execution.id

    @property
    def execution(self) -> NodeExecution:
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        return self._workflow_execution.get_node_execution(self.execution_id)

    @property
    def message(self) -> Optional[Message]:
        if self.message_id is None:
            return None
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        return self._workflow_execution.get_message(self.message_id)

    @property
    def parent(self) -> Optional["Step"]:
        if self.parent_step_id is None:
            return None
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        return self._workflow_execution.get_step(self.parent_step_id)

    @property
    def children(self) -> tuple["Step", ...]:
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        return tuple(
            self._workflow_execution.get_step(step_id)
            for step_id in self.child_step_ids
        )


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
    steps_by_id: Dict[UUID, Step] = Field(default_factory=dict)
    messages_by_id: Dict[UUID, Message] = Field(default_factory=dict)
    branches_by_id: Dict[UUID, BranchRecord] = Field(default_factory=dict)
    active_branch_id: Optional[UUID] = Field(default=None)
    step_ids: List[UUID] = Field(
        default_factory=list,
        description="Step ids visible on the active branch.",
    )
    llm_usage: Optional[LLMUsageStats] = Field(
        default=None, description="LLM usage stats for this workflow execution, if any."
    )
    last_step_llm_usage: Optional[LLMUsageStats] = Field(
        default=None,
        description="LLM usage stats for the last completed step, if any.",
    )
    last_user_input_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last user input message for this workflow.",
    )
    state: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _attach_runtime_refs_after_validation(self) -> "WorkflowExecution":
        return self.attach_runtime_refs()

    def iter_steps(self):
        for step_id in self.step_ids:
            yield self.get_step(step_id)

    def iter_steps_reversed(self):
        for step_id in reversed(self.step_ids):
            yield self.get_step(step_id)

    def iter_node_steps(self, execution_id: UUID):
        execution = self.get_node_execution(execution_id)
        for step_id in execution.step_ids:
            yield self.get_step(step_id)

    def iter_node_steps_reversed(self, execution_id: UUID):
        execution = self.get_node_execution(execution_id)
        for step_id in reversed(execution.step_ids):
            yield self.get_step(step_id)

    def iter_all_steps(self):
        yield from self.steps_by_id.values()

    def iter_all_steps_reversed(self):
        yield from reversed(tuple(self.steps_by_id.values()))

    def get_step(self, step_id: UUID) -> Step:
        step = self.steps_by_id.get(step_id)
        if step is None:
            raise KeyError(f"Unknown step id: {step_id}")
        return step

    def get_message(self, message_id: UUID) -> Message:
        message = self.messages_by_id.get(message_id)
        if message is None:
            raise KeyError(f"Unknown message id: {message_id}")
        return message

    def get_node_execution(self, execution_id: UUID) -> NodeExecution:
        execution = self.node_executions.get(execution_id)
        if execution is None:
            raise KeyError(f"Unknown node execution id: {execution_id}")
        return execution

    def get_branch(self, branch_id: UUID) -> BranchRecord:
        branch = self.branches_by_id.get(branch_id)
        if branch is None:
            raise KeyError(f"Unknown branch id: {branch_id}")
        return branch

    def get_step_ids(self) -> List[UUID]:
        return list(self.step_ids)

    def get_steps(self) -> tuple[Step, ...]:
        return tuple(self.get_step(step_id) for step_id in self.step_ids)

    def get_step_id_set(self) -> set[UUID]:
        return set(self.step_ids)

    def get_first_step(self) -> Optional[Step]:
        if not self.step_ids:
            return None
        return self.get_step(self.step_ids[0])

    def get_last_step(self) -> Optional[Step]:
        if not self.step_ids:
            return None
        return self.get_step(self.step_ids[-1])

    def get_step_index(self, step_id: UUID) -> int:
        return self.step_ids.index(step_id)

    def get_previous_step(self, step_id: UUID) -> Optional[Step]:
        index = self.get_step_index(step_id)
        if index == 0:
            return None
        return self.get_step(self.step_ids[index - 1])

    def get_next_step(self, step_id: UUID) -> Optional[Step]:
        index = self.get_step_index(step_id)
        if index + 1 >= len(self.step_ids):
            return None
        return self.get_step(self.step_ids[index + 1])

    def get_active_branch(self) -> BranchRecord:
        if self.active_branch_id is None:
            raise KeyError("No active branch id is set")
        return self.get_branch(self.active_branch_id)

    def attach_runtime_refs(self) -> "WorkflowExecution":
        for execution in self.node_executions.values():
            execution._workflow_execution = self
        for step in self.steps_by_id.values():
            step._workflow_execution = self
        return self

    def touch(self) -> None:
        self.updated_at = utcnow()
