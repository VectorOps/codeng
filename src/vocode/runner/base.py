from typing import (
    Optional,
    ClassVar,
    List,
    Any,
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
    chain: List[state.NodeExecution] = []
    current = execution
    while current is not None:
        chain.append(current)
        current = current.previous

    for exec_item in reversed(chain):
        for msg in exec_item.input_messages:
            yield msg, None
        for step in exec_item.steps:
            if step.message is None:
                continue
            yield step.message, step
