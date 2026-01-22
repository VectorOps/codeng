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
    need_input: bool = True
    need_input_prompt: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[models.Node] = Field(default_factory=list)
    edges: List[models.Edge] = Field(default_factory=list)
    agent_workflows: Optional[List[str]] = None

    @field_validator("nodes", mode="before")
    @classmethod
    def normalize_nodes(cls, v):
        if not isinstance(v, list):
            return v
        return [models.Node.from_node(item) for item in v]


class ToolCallFormatter(BaseModel):
    """
    Configures how to display a tool call in the terminal.
    - title: what to display as the function name
    - formatter: registered formatter implementation name (e.g. "generic")
    - show_output: whether to show tool output details by default
    - options: free-form formatter-specific configuration
    """

    title: str
    formatter: str = "generic"
    show_output: bool = False
    options: Dict[str, Any] = Field(default_factory=dict)


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




class TUIOptions(BaseModel):
    unicode: bool = True
    ascii_fallback: bool = False


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


class ShellMode(str, Enum):
    direct = "direct"
    shell = "shell"


class ShellSettings(BaseModel):
    # How shell commands are executed:
    # - "direct": each command runs in its own subprocess
    # - "shell": commands run via a long-lived shell with wrapped markers
    mode: ShellMode = ShellMode.shell
    # POSIX-only in v1; reserved for future shells
    type: Literal["bash"] = "bash"
    # Program and args to start the long-lived shell process
    program: str = "bash"
    args: List[str] = Field(default_factory=lambda: ["--noprofile", "--norc"])
    # Default per-command timeout (seconds)
    default_timeout_s: int = 120


class ProcessEnvSettings(BaseModel):
    inherit_parent: bool = True
    allowlist: Optional[List[str]] = None
    denylist: Optional[List[str]] = None
    defaults: Dict[str, str] = Field(default_factory=dict)


class ProcessSettings(BaseModel):
    # Backend key in the process backend registry. The backend is responsible
    # for spawning subprocesses via the EnvPolicy configured below.
    backend: Literal["local"] = "local"
    env: ProcessEnvSettings = Field(default_factory=ProcessEnvSettings)
    # Settings for long-lived interactive shells spawned through the process
    # backend.
    shell: ShellSettings = Field(default_factory=ShellSettings)


class ExecToolSettings(BaseModel):
    # Maximum characters of combined stdout/stderr returned by the exec tool.
    # This guards against excessive subprocess output overwhelming callers.
    max_output_chars: int = EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT
    # Optional default timeout (seconds) for exec tool invocations when the
    # tool spec does not provide a per-call override. None => use tool-level
    # constant default.
    timeout_s: Optional[float] = None


class ToolSettings(BaseModel):
    exec_tool: Optional[ExecToolSettings] = None


class PersistenceSettings(BaseModel):
    save_interval_s: float = 120.0
    max_total_log_bytes: int = 1024 * 1024 * 1024


class InternalHTTPSettings(BaseModel):
    host: str = "127.0.0.1"
    port: Optional[int] = None
    secret_key: Optional[str] = None


class Settings(BaseModel):
    workflows: Dict[str, WorkflowConfig] = Field(default_factory=dict)
    # Optional name of the workflow to auto-start in interactive UIs
    default_workflow: Optional[str] = Field(default=None)
    tools: List[ToolSpec] = Field(default_factory=list)
    # Tool settings
    tool_settings: Optional[ToolSettings] = Field(default=None)
    # Mapping of tool name -> formatter configuration
    tool_call_formatters: Dict[str, ToolCallFormatter] = Field(default_factory=dict)
    know: Optional[KnowProjectSettings] = Field(default=None)
    process: Optional[ProcessSettings] = Field(default=None)
    logging: Optional[LoggingSettings] = Field(default=None)
    persistence: Optional[PersistenceSettings] = Field(default=None)
    tui: Optional[TUIOptions] = Field(default=None)
    internal_http: Optional[InternalHTTPSettings] = Field(default=None)

    @model_validator(mode="after")
    def _sync_workflow_names(self) -> "Settings":
        for key, wf in self.workflows.items():
            wf.name = key
        return self
