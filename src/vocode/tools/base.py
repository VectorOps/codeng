from abc import ABC, abstractmethod
from typing import Any, Type, Dict, TYPE_CHECKING, Optional, Annotated, Union, ClassVar
from enum import Enum
from pydantic import BaseModel, Field
from vocode.state import Message, WorkflowExecution
from vocode.settings import ToolSpec

if TYPE_CHECKING:
    from vocode.project import Project


# Models
class ToolResponseType(str, Enum):
    text = "text"
    start_workflow = "start_workflow"


class ToolTextResponse(BaseModel):
    type: ToolResponseType = Field(default=ToolResponseType.text)
    text: Optional[str] = None


class ToolStartWorkflowResponse(BaseModel):
    type: ToolResponseType = Field(default=ToolResponseType.start_workflow)
    workflow: str
    # Optional initial text to seed the nested workflow (wrapped as a user Message).
    initial_text: Optional[str] = None
    # Optional advanced form; if provided, initial_text is ignored.
    initial_message: Optional[Message] = None


ToolResponse = Annotated[
    Union[ToolTextResponse, ToolStartWorkflowResponse],
    Field(discriminator="type"),
]


class ToolReq(BaseModel):
    execution: WorkflowExecution
    spec: ToolSpec

# Global registry of tool name -> tool instance
_registry: Dict[str, Type["BaseTool"]] = {}


class ToolFactory:
    _registry: ClassVar[Dict[str, Type["BaseTool"]]] = _registry

    @classmethod
    def register(
        cls,
        name: str,
        tool_cls: Type["BaseTool"] | None = None,
    ):
        def _do_register(inner: Type["BaseTool"]) -> Type["BaseTool"]:
            if name in cls._registry:
                raise ValueError(f"Tool with name '{name}' already registered.")
            cls._registry[name] = inner
            return inner

        if tool_cls is None:
            return _do_register
        return _do_register(tool_cls)

    @classmethod
    def unregister(cls, name: str) -> bool:
        return cls._registry.pop(name, None) is not None

    @classmethod
    def get(cls, name: str) -> Optional[Type["BaseTool"]]:
        return cls._registry.get(name)

    @classmethod
    def all(cls) -> Dict[str, Type["BaseTool"]]:
        return dict(cls._registry)


def register_tool(name: str, tool: Type["BaseTool"]) -> None:
    ToolFactory.register(name, tool)


def unregister_tool(name: str) -> bool:
    return ToolFactory.unregister(name)


def get_tool(name: str) -> Optional[Type["BaseTool"]]:
    return ToolFactory.get(name)


def get_all_tools() -> Dict[str, Type["BaseTool"]]:
    return ToolFactory.all()


class BaseTool(ABC):
    # Subclasses must set this to a unique string
    name: str

    def __init__(self, prj: "Project") -> None:
        self.prj = prj

    @abstractmethod
    async def run(self, req: ToolReq, args: Any) -> Optional[ToolResponse]:
        """
        Execute this tool within the context of the given Project.
        Args:
            project: The active project context.
            spec: ToolSpec including name, auto_approve, and optional config for this invocation.
            args: Parsed arguments structure (e.g., dict or Pydantic model). Not a JSON string.
        Returns:
            ToolResponse describing either final text, or a request to start a nested workflow.
        """
        pass

    @abstractmethod
    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        """
        Return this tool's definition in OpenAI 'function' tool format,
        using JSON Schema for parameters.
        """
        pass
