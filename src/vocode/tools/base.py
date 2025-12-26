from abc import ABC, abstractmethod
from typing import Any, Type, Dict, TYPE_CHECKING, Optional, Annotated, Union
from enum import Enum
from pydantic import BaseModel, Field
from vocode.state import Message
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

# Global registry of tool name -> tool instance
_registry: Dict[str, Type["BaseTool"]] = {}


def register_tool(name: str, tool: Type["BaseTool"]) -> None:
    """Registers a tool instance."""
    if name in _registry:
        raise ValueError(f"Tool with name '{name}' already registered.")
    _registry[name] = tool


def unregister_tool(name: str) -> bool:
    """Unregister a tool instance by name. Returns True if removed, False if not present."""
    return _registry.pop(name, None) is not None


def get_tool(name: str) -> Optional[Type["BaseTool"]]:
    """Gets a tool instance by name."""
    return _registry.get(name)


def get_all_tools() -> Dict[str, Type["BaseTool"]]:
    """Returns a copy of the tool registry."""
    return dict(_registry)


class BaseTool(ABC):
    # Subclasses must set this to a unique string
    name: str

    def __init__(self, prj: "Project") -> None:
        self.prj = prj

    @abstractmethod
    async def run(self, spec: ToolSpec, args: Any) -> Optional[ToolResponse]:
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
