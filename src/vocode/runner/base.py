from typing import (
    Optional,
    ClassVar,
    List,
    Any,
    Dict,
    Type,
    AsyncIterator,
    Iterator,
)
from uuid import UUID
from pydantic import BaseModel, Field
from .. import models, state


class ExecutorInput(BaseModel):
    execution: state.NodeExecution
    run: state.WorkflowExecution


class ExecutorFactory:
    _registry: ClassVar[dict[str, Type["BaseExecutor"]]] = {}

    @classmethod
    def register(
        cls,
        type_name: str,
        exec_cls: Type["BaseExecutor"] | None = None,
    ):
        if exec_cls is None:

            def decorator(inner: Type["BaseExecutor"]) -> Type["BaseExecutor"]:
                cls._registry[type_name] = inner
                return inner

            return decorator
        cls._registry[type_name] = exec_cls
        return exec_cls

    @classmethod
    def create_for_node(
        cls,
        node: models.Node,
        project: "Project",
    ) -> "BaseExecutor":
        sub = cls._registry.get(node.type)
        if sub is None:
            raise ValueError(f"No executor registered for node type '{node.type}'")
        return sub(config=node, project=project)


class BaseExecutor:
    type: ClassVar[Optional[str]] = None

    def __init__(self, config: models.Node, project: "Project"):
        """Initialize an executor instance with its corresponding Node config and Project."""
        self.config = config
        self.project = project
        self.workflow_execution_id: Optional[str] = None
        self.workflow_name: Optional[str] = None

    def bind_run_context(
        self,
        workflow_execution_id: str,
        workflow_name: str,
    ) -> None:
        self.workflow_execution_id = workflow_execution_id
        self.workflow_name = workflow_name

    def clear_run_context(self) -> None:
        self.workflow_execution_id = None
        self.workflow_name = None

    async def init(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def get_available_tools(self) -> Dict[str, Any]:
        return {}

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        """
        Async generator from Executor to Runner. Executors yields steps. If step is alread
        is in the state, then it's updated. If it is a new state - it's appended to the state.
        """
        raise NotImplementedError(
            "Executor subclasses must implement 'run' as an async generator"
        )


def iter_execution_messages(
    execution: state.NodeExecution,
) -> Iterator[tuple[state.Message, Optional[state.Step]]]:
    workflow_execution = execution._workflow_execution
    if workflow_execution is None:
        raise ValueError("NodeExecution is not attached to a workflow execution")
    visible_step_ids = workflow_execution.get_step_ids()
    chain: List[state.NodeExecution] = []
    current: Optional[state.NodeExecution] = execution
    while current is not None:
        chain.append(current)
        current = current.previous
    ordered_chain = list(reversed(chain))
    ordered_execution_ids = {exec_item.id for exec_item in ordered_chain}
    visible_steps = [
        workflow_execution.get_step(step_id)
        for step_id in visible_step_ids
        if workflow_execution.get_step(step_id).execution_id in ordered_execution_ids
    ]
    visible_steps_by_execution_id: Dict[UUID, List[state.Step]] = {}
    visible_step_ids_by_execution_id: Dict[UUID, set[UUID]] = {}
    for step in visible_steps:
        execution_steps = visible_steps_by_execution_id.get(step.execution_id)
        if execution_steps is None:
            execution_steps = []
            visible_steps_by_execution_id[step.execution_id] = execution_steps
        execution_steps.append(step)
        execution_step_ids = visible_step_ids_by_execution_id.get(step.execution_id)
        if execution_step_ids is None:
            execution_step_ids = set()
            visible_step_ids_by_execution_id[step.execution_id] = execution_step_ids
        execution_step_ids.add(step.id)
    visible_input_message_ids = {
        step.message_id
        for step in visible_steps
        if step.type == state.StepType.INPUT_MESSAGE and step.message_id is not None
    }
    for exec_item in ordered_chain:
        for message in exec_item.input_messages:
            if message.id in visible_input_message_ids:
                continue
            yield message, None
        for step in visible_steps_by_execution_id.get(exec_item.id, []):
            if step.message is None:
                continue
            yield step.message, step
