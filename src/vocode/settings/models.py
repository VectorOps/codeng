from typing import List, Dict, Optional, Any, Union, Set, Final, Type, Literal
from enum import Enum
import re
from pathlib import Path
from os import PathLike
import os
import json
from importlib import resources
from pydantic import BaseModel, Field
from pydantic import model_validator, field_validator
import yaml
import json5  # type: ignore
from vocode import models
from vocode.lib.validators import get_value_by_dotted_key, regex_matches_value


from knowlt.settings import ProjectSettings as KnowProjectSettings


# Base path for packaged template configs, e.g. include: { vocode: "nodes/requirements.yaml" }
VOCODE_TEMPLATE_BASE: Path = (resources.files("vocode") / "config_templates").resolve()

# Include spec keys for bundled templates. Support GitLab 'template', legacy 'vocode', and 'templates'
TEMPLATE_INCLUDE_KEYS: Final[Set[str]] = {"template", "templates", "vocode"}

# Variable replacement pattern.
# Supports:
#   - ${NAME}
#   - ${env:NAME}
# Ignores '$${NAME}' so it can be used to escape a literal '${NAME}'.
VAR_PATTERN = re.compile(
    r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?)\}"
)

INCLUDE_KEY: Final[str] = "$include"

# Default maximum combined stdout/stderr characters returned by the exec tool.
# Individual projects can override this via Settings.exec_tool.max_output_chars.
EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT: Final[int] = 10 * 1024


class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class WorkflowConfig(BaseModel):
    name: Optional[str] = None
    # Human-readable purpose/summary for this workflow; used in tool descriptions.
    description: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[models.Node] = Field(default_factory=list)
    edges: List[models.Edge] = Field(default_factory=list)
    agent_workflows: Optional[List[str]] = None


class ToolAutoApproveRule(BaseModel):
    """Rule for automatically approving a tool call based on its JSON arguments.

    - key: dot-separated path inside the arguments dict (e.g. "resource.action").
    - pattern: regular expression applied to the stringified value at that key.
    """

    key: str
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, v: str) -> str:
        """Validate that 'pattern' is a syntactically correct regular expression."""
        try:
            re.compile(v)
        except (
            re.error
        ) as exc:  # pragma: no cover - exact message is implementation detail
            raise ValueError(f"Invalid regex pattern {v!r}: {exc}") from exc
        return v


class ToolSpec(BaseModel):
    """
    Tool specification usable both globally (Settings.tools) and per-node (LLMNode.tools).

    When updating the model, make sure that build_effective_tool_specs helper function
    is also updated.
    """

    name: str
    enabled: bool = True
    auto_approve: Optional[bool] = None
    auto_approve_rules: List[ToolAutoApproveRule] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"name": v}
        if isinstance(v, dict):
            # Permit extra fields; validator ignores unknowns via Pydantic defaults
            name = v.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("Tool spec must include non-empty 'name'")
            out = {
                "name": name,
                "enabled": v.get("enabled", True),
                "auto_approve": v.get("auto_approve", None),
                "auto_approve_rules": v.get("auto_approve_rules", []) or [],
                "config": v.get("config", {}) or {},
            }
            return out
        return v


class LoggingSettings(BaseModel):
    # Default level for our primary loggers (vocode, knowlt) if not overridden.
    default_level: LogLevel = LogLevel.info
    # Mapping of logger name -> level override (e.g., {"asyncio": "debug"})
    enabled_loggers: Dict[str, LogLevel] = Field(default_factory=dict)


class Settings(BaseModel):
    workflows: Dict[str, WorkflowConfig] = Field(default_factory=dict)
    # Optional name of the workflow to auto-start in interactive UIs
    default_workflow: Optional[str] = Field(default=None)
    tools: List[ToolSpec] = Field(default_factory=list)
    know: Optional[KnowProjectSettings] = Field(default=None)
    # Optional logging configuration (per-logger overrides).
    logging: Optional[LoggingSettings] = Field(default=None)
    # Optional Model Context Protocol (MCP) configuration

    @model_validator(mode="after")
    def _sync_workflow_names(self) -> "Settings":
        for key, wf in self.workflows.items():
            wf.name = key
        return self
