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

    def get_active_branch(self) -> BranchRecord:
        return self._ensure_default_branch()

    def get_branch_step_ids(self, branch_id: UUID) -> List[UUID]:
        return self._compute_branch_step_ids(branch_id)

    def get_active_step_ids(self) -> List[UUID]:
        return list(self.step_ids)

    def refresh_visible_step_ids(self) -> None:
        branch = self._ensure_default_branch()
        self.step_ids = self._compute_branch_step_ids(branch.id)
        for execution in self.node_executions.values():
            execution.step_ids = [
                step_id
                for step_id in self.step_ids
                if self.get_step(step_id).execution_id == execution.id
            ]

    def _compute_branch_step_ids(self, branch_id: UUID) -> List[UUID]:
        branch = self.get_branch(branch_id)
        if branch.head_step_id is None:
            return []
        step_ids: List[UUID] = []
        seen: set[UUID] = set()
        current_step_id: Optional[UUID] = branch.head_step_id
        while current_step_id is not None:
            if current_step_id in seen:
                break
            seen.add(current_step_id)
            step_ids.append(current_step_id)
            current_step_id = self.get_step(current_step_id).parent_step_id
        step_ids.reverse()
        return step_ids

    def get_active_steps(self) -> tuple[Step, ...]:
        return tuple(self.get_step(step_id) for step_id in self.get_active_step_ids())

    def get_active_step_id_set(self) -> set[UUID]:
        return set(self.get_active_step_ids())

    def get_active_head_step(self) -> Optional[Step]:
        branch = self._ensure_default_branch()
        if branch.head_step_id is None:
            return None
        return self.get_step(branch.head_step_id)

    def switch_branch(self, branch_id: UUID) -> BranchRecord:
        branch = self.get_branch(branch_id)
        self.active_branch_id = branch.id
        self.refresh_visible_step_ids()
        return branch

    def create_branch(
        self,
        *,
        head_step_id: Optional[UUID] = None,
        base_step_id: Optional[UUID] = None,
        label: Optional[str] = None,
        activate: bool = True,
    ) -> BranchRecord:
        branch = BranchRecord(
            head_step_id=head_step_id,
            base_step_id=base_step_id,
            label=label,
        )
        self.branches_by_id[branch.id] = branch
        if activate:
            self.active_branch_id = branch.id
        self.refresh_visible_step_ids()
        return branch

    def update_active_branch_head(self, step_id: Optional[UUID]) -> BranchRecord:
        branch = self._ensure_default_branch()
        branch.head_step_id = step_id
        if branch.base_step_id is None:
            branch.base_step_id = step_id
        self.refresh_visible_step_ids()
        return branch

    def find_lowest_common_ancestor(
        self,
        old_head_step_id: Optional[UUID],
        new_head_step_id: Optional[UUID],
    ) -> Optional[UUID]:
        old_path = self._get_path_from_head(old_head_step_id)
        new_path = self._get_path_from_head(new_head_step_id)
        lca: Optional[UUID] = None
        for old_step_id, new_step_id in zip(old_path, new_path):
            if old_step_id != new_step_id:
                break
            lca = old_step_id
        return lca

    def add_message(self, message: Message) -> Message:
        self.messages_by_id[message.id] = message
        return message

    def add_node_execution(self, execution: NodeExecution) -> NodeExecution:
        execution._workflow_execution = self
        self.node_executions[execution.id] = execution
        return execution

    def add_step(self, step: Step) -> Step:
        if step.execution_id not in self.node_executions:
            raise KeyError(f"Unknown node execution id: {step.execution_id}")
        if step.message_id is not None and step.message_id not in self.messages_by_id:
            raise KeyError(f"Unknown message id: {step.message_id}")
        branch = self._ensure_default_branch()
        if step.parent_step_id is None:
            step.parent_step_id = branch.head_step_id
        if step.parent_step_id is not None:
            parent_step = self.get_step(step.parent_step_id)
            if step.id not in parent_step.child_step_ids:
                parent_step.child_step_ids.append(step.id)
        step._workflow_execution = self
        self.steps_by_id[step.id] = step
        execution = self.node_executions[step.execution_id]
        branch.head_step_id = step.id
        if branch.base_step_id is None:
            branch.base_step_id = step.id
        self.refresh_visible_step_ids()
        return step

    def attach_runtime_refs(self) -> "WorkflowExecution":
        self._normalize_tree_state()
        for execution in self.node_executions.values():
            execution._workflow_execution = self
        for step in self.steps_by_id.values():
            step._workflow_execution = self
        self.refresh_visible_step_ids()
        return self

    def create_node_execution(self, **data: Any) -> NodeExecution:
        input_messages = data.pop("input_messages", None)
        if input_messages is not None:
            for message in input_messages:
                self.add_message(message)
            data["input_message_ids"] = [message.id for message in input_messages]
        execution = NodeExecution(workflow_execution=self, **data)
        return self.add_node_execution(execution)

    def create_step(self, **data: Any) -> Step:
        message = data.pop("message", None)
        if message is not None:
            self.add_message(message)
            data["message_id"] = message.id
        step = Step(workflow_execution=self, **data)
        return self.add_step(step)

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
        removed_parent_ids: Dict[UUID, Optional[UUID]] = {}
        for step in self.steps_by_id.values():
            remaining_child_ids = [
                child_step_id
                for child_step_id in step.child_step_ids
                if child_step_id not in step_ids_set
            ]
            if len(remaining_child_ids) != len(step.child_step_ids):
                step.child_step_ids = remaining_child_ids
        for step in list(self.steps_by_id.values()):
            if step.id in step_ids_set:
                removed_parent_ids[step.id] = step.parent_step_id
                self.steps_by_id.pop(step.id, None)
            else:
                if step.parent_step_id in step_ids_set:
                    step.parent_step_id = None
        for branch in self.branches_by_id.values():
            if branch.head_step_id in step_ids_set:
                branch.head_step_id = self._find_visible_ancestor(
                    branch.head_step_id,
                    removed_parent_ids,
                )
            if branch.base_step_id in step_ids_set:
                branch.base_step_id = self._find_visible_ancestor(
                    branch.base_step_id,
                    removed_parent_ids,
                )
        self._ensure_default_branch()
        self.refresh_visible_step_ids()

    def delete_node_execution(self, execution_id: UUID) -> None:
        execution = self.node_executions.get(execution_id)
        if execution is None:
            return
        step_ids = [
            step.id
            for step in self.steps_by_id.values()
            if step.execution_id == execution_id
        ]
        if step_ids:
            self.delete_steps(step_ids)
        if execution_id in self.node_executions:
            del self.node_executions[execution_id]

    def trim_empty_node_executions(self) -> None:
        empty_execution_ids: List[UUID] = []
        for execution_id, execution in self.node_executions.items():
            has_steps = False
            for step in self.steps_by_id.values():
                if step.execution_id == execution_id:
                    has_steps = True
                    break
            if not has_steps:
                empty_execution_ids.append(execution_id)
        for execution_id in empty_execution_ids:
            self.delete_node_execution(execution_id)

    def _get_path_from_head(self, step_id: Optional[UUID]) -> List[UUID]:
        if step_id is None:
            return []
        path: List[UUID] = []
        seen: set[UUID] = set()
        current_step_id: Optional[UUID] = step_id
        while current_step_id is not None:
            if current_step_id in seen:
                break
            seen.add(current_step_id)
            path.append(current_step_id)
            step = self.steps_by_id.get(current_step_id)
            if step is None:
                break
            current_step_id = step.parent_step_id
        path.reverse()
        return path

    def _ensure_default_branch(self) -> BranchRecord:
        if self.active_branch_id is not None:
            branch = self.branches_by_id.get(self.active_branch_id)
            if branch is not None:
                return branch
        if self.branches_by_id:
            branch = next(iter(self.branches_by_id.values()))
            self.active_branch_id = branch.id
            return branch
        ordered_steps = tuple(self.iter_all_steps())
        head_step_id = ordered_steps[-1].id if ordered_steps else None
        base_step_id = None
        if ordered_steps:
            base_step_id = self._get_path_from_head(head_step_id)[0]
        branch = BranchRecord(
            head_step_id=head_step_id,
            base_step_id=base_step_id,
        )
        self.branches_by_id[branch.id] = branch
        self.active_branch_id = branch.id
        return branch

    def _find_visible_ancestor(
        self,
        step_id: Optional[UUID],
        removed_parent_ids: Optional[Dict[UUID, Optional[UUID]]] = None,
    ) -> Optional[UUID]:
        current_step_id = step_id
        while current_step_id is not None:
            step = self.steps_by_id.get(current_step_id)
            if step is not None:
                return current_step_id
            if removed_parent_ids is None:
                return None
            current_step_id = removed_parent_ids.get(current_step_id)
        return None

    def _normalize_tree_state(self) -> None:
        has_any_parent = any(
            step.parent_step_id is not None for step in self.steps_by_id.values()
        )
        ordered_steps = tuple(self.iter_all_steps())
        if not has_any_parent and ordered_steps:
            previous_step_id: Optional[UUID] = None
            for step in ordered_steps:
                step.parent_step_id = previous_step_id
                previous_step_id = step.id
        child_ids_by_parent: Dict[UUID, List[UUID]] = {}
        for step in self.steps_by_id.values():
            step.child_step_ids = []
        for step in ordered_steps:
            if step.parent_step_id is None:
                continue
            child_ids = child_ids_by_parent.get(step.parent_step_id)
            if child_ids is None:
                child_ids = []
                child_ids_by_parent[step.parent_step_id] = child_ids
            child_ids.append(step.id)
        for parent_step_id, child_ids in child_ids_by_parent.items():
            parent_step = self.steps_by_id.get(parent_step_id)
            if parent_step is None:
                continue
            parent_step.child_step_ids = child_ids
        branch = self._ensure_default_branch()
        if branch.head_step_id is None and ordered_steps:
            branch.head_step_id = ordered_steps[-1].id
        if branch.base_step_id is None and branch.head_step_id is not None:
            path = self._get_path_from_head(branch.head_step_id)
            if path:
                branch.base_step_id = path[0]
