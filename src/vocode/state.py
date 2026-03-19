import collections.abc as abc
from enum import Enum
from typing import List, Optional, Annotated, Dict, Any, Union
from pydantic import BaseModel, Field, StringConstraints
from pydantic import PrivateAttr
from pydantic import model_validator
from datetime import datetime
from .lib.date import utcnow
from uuid import UUID, uuid4
from .settings import ToolSpec  # type: ignore
from .models import OutputMode, Role, StepContentType


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


_UNSET = object()


class _ReferenceList(abc.MutableSequence):
    def __init__(
        self,
        ids: List[UUID],
        resolver: abc.Callable[[UUID], Any],
        id_getter: abc.Callable[[Any], UUID],
        binder: abc.Callable[[Any], None],
        on_change: abc.Callable[[], None],
    ) -> None:
        self._ids = ids
        self._resolver = resolver
        self._id_getter = id_getter
        self._binder = binder
        self._on_change = on_change

    def __getitem__(self, index: Union[int, slice]) -> Any:
        if isinstance(index, slice):
            return [self._resolver(item_id) for item_id in self._ids[index]]
        return self._resolver(self._ids[index])

    def __setitem__(self, index: Union[int, slice], value: Any) -> None:
        values = value if isinstance(index, slice) else [value]
        new_ids: List[UUID] = []
        for item in values:
            self._binder(item)
            new_ids.append(self._id_getter(item))
        if isinstance(index, slice):
            self._ids[index] = new_ids
        else:
            self._ids[index] = new_ids[0]
        self._on_change()

    def __delitem__(self, index: Union[int, slice]) -> None:
        del self._ids[index]
        self._on_change()

    def __len__(self) -> int:
        return len(self._ids)

    def insert(self, index: int, value: Any) -> None:
        self._binder(value)
        self._ids.insert(index, self._id_getter(value))
        self._on_change()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return super().__eq__(other)


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

    @property
    def tool_call_request_ids(self) -> List[str]:
        return [tool_call.id for tool_call in self.tool_call_requests]

    @property
    def tool_call_response_ids(self) -> List[str]:
        return [tool_call.id for tool_call in self.tool_call_responses]


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
    input_message_ids: List[UUID] = Field(
        default_factory=list,
        description="Initial input message ids for this node",
    )
    step_ids: List[UUID] = Field(default_factory=list, description="A list of step ids")
    status: RunStatus = Field(..., description="Node execution status")
    state: Optional[BaseModel] = Field(
        default=None,
        description="Any internal state that is maintained by the corresponding step runner.",
    )
    created_at: datetime = Field(default_factory=utcnow)
    _workflow_execution: Optional["WorkflowExecution"] = PrivateAttr(default=None)
    _previous: Optional["NodeExecution"] = PrivateAttr(default=None)
    _input_messages_by_id: Dict[UUID, Message] = PrivateAttr(default_factory=dict)
    _steps_by_id: Dict[UUID, "Step"] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        workflow_execution: Optional["WorkflowExecution"] = None,
        **data: Any,
    ) -> None:
        previous = data.pop("previous", _UNSET)
        input_messages = data.pop("input_messages", _UNSET)
        steps = data.pop("steps", _UNSET)
        if previous is not _UNSET:
            data["previous_id"] = None if previous is None else previous.id
        if input_messages is not _UNSET:
            data["input_message_ids"] = [message.id for message in input_messages]
        if steps is not _UNSET:
            data["step_ids"] = [step.id for step in steps]
        super().__init__(**data)
        self._workflow_execution = workflow_execution
        if previous is not _UNSET:
            self._previous = previous
        if input_messages is not _UNSET:
            for message in input_messages:
                self._bind_input_message(message)
        if steps is not _UNSET:
            for step in steps:
                self._bind_step(step)

    def model_copy(
        self, *, update: Optional[Dict[str, Any]] = None, deep: bool = False
    ) -> "NodeExecution":
        effective_update = dict(update or {})
        previous = effective_update.pop("previous", _UNSET)
        input_messages = effective_update.pop("input_messages", _UNSET)
        steps = effective_update.pop("steps", _UNSET)
        if previous is not _UNSET:
            effective_update["previous_id"] = None if previous is None else previous.id
        if input_messages is not _UNSET:
            effective_update["input_message_ids"] = [
                message.id for message in input_messages
            ]
        if steps is not _UNSET:
            effective_update["step_ids"] = [step.id for step in steps]
        copied = super().model_copy(update=effective_update, deep=deep)
        copied._workflow_execution = self._workflow_execution
        copied._previous = self._previous if previous is _UNSET else previous
        copied._input_messages_by_id = dict(self._input_messages_by_id)
        copied._steps_by_id = dict(self._steps_by_id)
        if input_messages is not _UNSET:
            copied._input_messages_by_id = {
                message.id: message for message in input_messages
            }
        if steps is not _UNSET:
            copied._steps_by_id = {step.id: step for step in steps}
        return copied

    @property
    def workflow_execution_id(self) -> UUID:
        return self._workflow_execution.id

    @property
    def previous(self) -> Optional["NodeExecution"]:
        if self.previous_id is None:
            return None
        if self._previous is not None and self._previous.id == self.previous_id:
            return self._previous
        return self._resolve_previous(self.previous_id)

    @property
    def input_messages(self) -> List[Message]:
        return _ReferenceList(
            self.input_message_ids,
            self._resolve_input_message,
            lambda message: message.id,
            self._bind_input_message,
            self._touch_workflow,
        )

    @property
    def steps(self) -> List["Step"]:
        return _ReferenceList(
            self.step_ids,
            self._resolve_step,
            lambda step: step.id,
            self._bind_step,
            self._touch_workflow,
        )

    @previous.setter
    def previous(self, value: Optional["NodeExecution"]) -> None:
        raise AttributeError(
            "previous is a read-only reference; set previous_id instead"
        )

    @input_messages.setter
    def input_messages(self, value: List[Message]) -> None:
        raise AttributeError(
            "input_messages is a read-only reference; set input_message_ids instead"
        )

    @steps.setter
    def steps(self, value: List["Step"]) -> None:
        raise AttributeError("steps is a read-only reference; set step_ids instead")

    def _touch_workflow(self) -> None:
        if self._workflow_execution is not None:
            self._workflow_execution.touch()

    def _bind_input_message(self, message: Message) -> None:
        self._input_messages_by_id[message.id] = message
        if self._workflow_execution is not None:
            self._workflow_execution.messages_by_id[message.id] = message

    def _bind_step(self, step: "Step") -> None:
        self._steps_by_id[step.id] = step
        step._execution = self
        if self._workflow_execution is not None:
            step._workflow_execution = self._workflow_execution
            self._workflow_execution._bind_step_ref(step)

    def _resolve_previous(self, execution_id: UUID) -> "NodeExecution":
        if self._previous is not None and self._previous.id == execution_id:
            return self._previous
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        previous = self._workflow_execution.node_executions.get(execution_id)
        if previous is None:
            raise KeyError(f"Unknown node execution id: {execution_id}")
        self._previous = previous
        return previous

    def _resolve_input_message(self, message_id: UUID) -> Message:
        message = self._input_messages_by_id.get(message_id)
        if message is not None:
            return message
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        message = self._workflow_execution.messages_by_id.get(message_id)
        if message is None:
            raise KeyError(f"Unknown message id: {message_id}")
        self._input_messages_by_id[message_id] = message
        return message

    def _resolve_step(self, step_id: UUID) -> "Step":
        step = self._steps_by_id.get(step_id)
        if step is not None:
            return step
        if self._workflow_execution is None:
            raise ValueError("NodeExecution is not attached to a workflow execution")
        step = self._workflow_execution.steps_by_id.get(step_id)
        if step is None:
            raise KeyError(f"Unknown step id: {step_id}")
        self._steps_by_id[step_id] = step
        return step


class Step(BaseModel):
    """
    This class epresents a single execution step in the workflow. Any LLM
    message, any user input, etc will generate a step.
    """

    id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for this step"
    )
    execution_id: UUID = Field(..., description="Node execution id for this step")
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
    _execution: Optional[NodeExecution] = PrivateAttr(default=None)
    _message: Optional[Message] = PrivateAttr(default=None)

    def __init__(
        self,
        workflow_execution: Optional["WorkflowExecution"] = None,
        **data: Any,
    ) -> None:
        execution = data.pop("execution", _UNSET)
        message = data.pop("message", _UNSET)
        if execution is not _UNSET:
            data["execution_id"] = execution.id
        if message is not _UNSET:
            data["message_id"] = None if message is None else message.id
        super().__init__(**data)
        self._workflow_execution = workflow_execution
        if execution is not _UNSET:
            self._execution = execution
        if message is not _UNSET:
            self._message = message

    def model_copy(
        self, *, update: Optional[Dict[str, Any]] = None, deep: bool = False
    ) -> "Step":
        effective_update = dict(update or {})
        execution = effective_update.pop("execution", _UNSET)
        message = effective_update.pop("message", _UNSET)
        if execution is not _UNSET:
            effective_update["execution_id"] = execution.id
        if message is not _UNSET:
            effective_update["message_id"] = None if message is None else message.id
        copied = super().model_copy(update=effective_update, deep=deep)
        copied._workflow_execution = self._workflow_execution
        copied._execution = self._execution if execution is _UNSET else execution
        copied._message = self._message if message is _UNSET else message
        return copied

    @property
    def workflow_execution_id(self) -> UUID:
        return self._workflow_execution.id

    @property
    def execution(self) -> NodeExecution:
        if self._execution is not None and self._execution.id == self.execution_id:
            return self._execution
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        execution = self._workflow_execution.node_executions.get(self.execution_id)
        if execution is None:
            raise KeyError(f"Unknown node execution id: {self.execution_id}")
        self._execution = execution
        return execution

    @execution.setter
    def execution(self, value: NodeExecution) -> None:
        raise AttributeError(
            "execution is a read-only reference; set execution_id instead"
        )

    @property
    def message(self) -> Optional[Message]:
        if self.message_id is None:
            return None
        if self._message is not None and self._message.id == self.message_id:
            return self._message
        if self._workflow_execution is None:
            raise ValueError("Step is not attached to a workflow execution")
        message = self._workflow_execution.messages_by_id.get(self.message_id)
        if message is None:
            raise KeyError(f"Unknown message id: {self.message_id}")
        self._message = message
        return message

    @message.setter
    def message(self, value: Optional[Message]) -> None:
        raise AttributeError("message is a read-only reference; set message_id instead")


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
    step_ids: List[UUID] = Field(default_factory=list, description="A list of step ids")
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

    @property
    def steps(self) -> List[Step]:
        return _ReferenceList(
            self.step_ids,
            self._resolve_step,
            lambda step: step.id,
            self._bind_step_ref,
            self.touch,
        )

    @steps.setter
    def steps(self, value: List[Step]) -> None:
        raise AttributeError("steps is a read-only reference; set step_ids instead")

    def _bind_step(self, step: Step) -> None:
        self._bind_step_ref(step)
        execution = self.node_executions.get(step.execution_id)
        if execution is not None:
            execution._bind_step(step)
            if step.id not in execution.step_ids:
                execution.step_ids.append(step.id)
        if step.message is not None:
            self.messages_by_id[step.message.id] = step.message

    def _bind_step_ref(self, step: Step) -> None:
        step._workflow_execution = self
        self.steps_by_id[step.id] = step
        self.node_executions[step.execution.id] = step.execution
        if step.message is not None:
            self.messages_by_id[step.message.id] = step.message

    def _resolve_step(self, step_id: UUID) -> Step:
        step = self.steps_by_id.get(step_id)
        if step is None:
            raise KeyError(f"Unknown step id: {step_id}")
        return step

    def attach_runtime_refs(self) -> "WorkflowExecution":
        for execution in self.node_executions.values():
            execution._workflow_execution = self
            if execution._previous is not None:
                execution.previous_id = execution._previous.id
            for message in list(execution._input_messages_by_id.values()):
                self.messages_by_id[message.id] = message
            for message_id in execution.input_message_ids:
                message = execution._input_messages_by_id.get(message_id)
                if message is not None:
                    self.messages_by_id[message.id] = message
        for step in list(self.steps_by_id.values()):
            step._workflow_execution = self
            if step._execution is not None:
                step.execution_id = step._execution.id
                self.node_executions[step._execution.id] = step._execution
            if step._message is not None:
                step.message_id = step._message.id
                self.messages_by_id[step._message.id] = step._message
        for execution in self.node_executions.values():
            for step in list(execution._steps_by_id.values()):
                self._bind_step_ref(step)
                if step.id not in execution.step_ids:
                    execution.step_ids.append(step.id)
            for step_id in execution.step_ids:
                step = self.steps_by_id.get(step_id)
                if step is not None:
                    execution._steps_by_id[step_id] = step
        for step_id in self.step_ids:
            step = self.steps_by_id.get(step_id)
            if step is not None:
                self._bind_step_ref(step)
        return self

    def create_node_execution(self, **data: Any) -> NodeExecution:
        execution = NodeExecution(workflow_execution=self, **data)
        self.node_executions[execution.id] = execution
        for message in execution.input_messages:
            self.messages_by_id[message.id] = message
        return execution

    def create_step(self, **data: Any) -> Step:
        step = Step(workflow_execution=self, **data)
        self._bind_step(step)
        if step.id not in self.step_ids:
            self.step_ids.append(step.id)
        return step

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
                self.steps_by_id.pop(step.id, None)
                execution_id = step.execution_id
                if execution_id in self.node_executions:
                    if execution_id not in executions_step_ids:
                        executions_step_ids[execution_id] = []
                    executions_step_ids[execution_id].append(step.id)
            else:
                remaining_steps.append(step)
        self.step_ids = [step.id for step in remaining_steps]
        for execution_id, removed_ids in executions_step_ids.items():
            execution = self.node_executions.get(execution_id)
            if execution is not None:
                removed_set = set(removed_ids)
                execution.step_ids = [
                    current_step_id
                    for current_step_id in execution.step_ids
                    if current_step_id not in removed_set
                ]
                for removed_step_id in removed_set:
                    execution._steps_by_id.pop(removed_step_id, None)

    def delete_node_execution(self, execution_id: UUID) -> None:
        execution = self.node_executions.get(execution_id)
        if execution is None:
            return
        step_ids = list(execution.step_ids)
        if step_ids:
            self.delete_steps(step_ids)
        if execution_id in self.node_executions:
            del self.node_executions[execution_id]

    def trim_empty_node_executions(self) -> None:
        empty_execution_ids: List[UUID] = []
        for execution_id, execution in self.node_executions.items():
            if not execution.step_ids:
                empty_execution_ids.append(execution_id)
        for execution_id in empty_execution_ids:
            self.delete_node_execution(execution_id)
