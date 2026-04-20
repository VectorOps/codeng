from typing import (
    Annotated,
    List,
    Dict,
    Optional,
    Any,
    Union,
    Set,
    Final,
    Type,
    Literal,
)
import re
from enum import Enum
from pathlib import Path
from os import PathLike
import os
import json
from importlib import resources
from pydantic import BaseModel, Field, PrivateAttr
from pydantic import model_validator, field_validator
import yaml
import json5  # type: ignore
from vocode import models as vocode_models
from vocode.lib.validators import get_value_by_dotted_key, regex_matches_value
from vocode import vars as vars_mod
from vocode import vars_values as vars_values_mod
from vocode.vars import VAR_PATTERN


from knowlt.settings import ProjectSettings as KnowProjectSettings


# Base path for packaged template configs, e.g. include: { vocode: "nodes/requirements.yaml" }
VOCODE_TEMPLATE_BASE: Path = (resources.files("vocode") / "config_templates").resolve()

# Include spec keys for bundled templates. Support GitLab 'template', legacy 'vocode', and 'templates'
TEMPLATE_INCLUDE_KEYS: Final[Set[str]] = {"template", "templates", "vocode"}

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
    disabled = "disabled"


class WorkflowConfig(vars_mod.BaseVarModel):
    name: Optional[str] = None
    # Human-readable purpose/summary for this workflow; used in tool descriptions.
    description: Optional[str] = None
    need_input: bool = True
    need_input_prompt: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[vocode_models.Node] = Field(default_factory=list)
    edges: List[vocode_models.Edge] = Field(default_factory=list)
    agents: Optional[List[str]] = None
    mcp: Optional["MCPWorkflowSettings"] = None

    @field_validator("nodes", mode="before")
    @classmethod
    def normalize_nodes(cls, v):
        if not isinstance(v, list):
            return v
        return [vocode_models.Node.from_node(item) for item in v]


class ToolCallFormatter(vars_mod.BaseVarModel):
    """
    Configures how to display a tool call in the terminal.
    - title: what to display as the function name
    - formatter: registered formatter implementation name (e.g. "generic")
    - show_output: whether to show tool output details by default
    - show_execution_stats: whether to show execution statistics (duration, status)
      for this tool request when rendered in the TUI
    - options: free-form formatter-specific configuration
    """

    title: str
    formatter: str = "generic"
    show_output: bool = False
    show_execution_stats: bool = True
    options: Dict[str, Any] = Field(default_factory=dict)


class ToolAutoApproveRule(vars_mod.BaseVarModel):
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


class MarkdownRenderMode(str, Enum):
    rich_markdown = "rich_markdown"
    syntax = "syntax"


class TUIOptions(vars_mod.BaseVarModel):
    unicode: bool = True
    ascii_fallback: bool = False
    expand_confirm_tools: bool = True
    submit_with_enter: bool = True
    banner_text: str = "VOCODE"
    banner_font: str = "spliff"
    markdown_render_mode: MarkdownRenderMode = MarkdownRenderMode.rich_markdown
    full_refresh_max_lines: Optional[int] = Field(default=None, ge=1)

    full_refresh_max_components: Optional[int] = Field(default=None, ge=1)


class ToolSpec(vars_mod.BaseVarModel):
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


class LoggingSettings(vars_mod.BaseVarModel):
    # Default level for our primary loggers (vocode, knowlt) if not overridden.
    default_level: LogLevel = LogLevel.info
    # Mapping of logger name -> level override (e.g., {"asyncio": "debug"})
    enabled_loggers: Dict[str, LogLevel] = Field(
        default_factory=lambda: {
            "connect": LogLevel.critical,
            "aiohttp": LogLevel.warning,
        }
    )


class ShellMode(str, Enum):
    direct = "direct"
    shell = "shell"


class ShellSettings(vars_mod.BaseVarModel):
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


class ProcessEnvSettings(vars_mod.BaseVarModel):
    inherit_parent: bool = True
    allowlist: Optional[List[str]] = None
    denylist: Optional[List[str]] = None
    defaults: Dict[str, str] = Field(default_factory=dict)


class ProcessSettings(vars_mod.BaseVarModel):
    # Backend key in the process backend registry. The backend is responsible
    # for spawning subprocesses via the EnvPolicy configured below.
    backend: Literal["local"] = "local"
    env: ProcessEnvSettings = Field(default_factory=ProcessEnvSettings)
    # Settings for long-lived interactive shells spawned through the process
    # backend.
    shell: ShellSettings = Field(default_factory=ShellSettings)


class ExecToolSettings(vars_mod.BaseVarModel):
    # Maximum characters of combined stdout/stderr returned by the exec tool.
    # This guards against excessive subprocess output overwhelming callers.
    max_output_chars: int = EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT
    # Optional default timeout (seconds) for exec tool invocations when the
    # tool spec does not provide a per-call override. None => use tool-level
    # constant default.
    timeout_s: Optional[float] = None


class ToolSettings(vars_mod.BaseVarModel):
    exec_tool: Optional[ExecToolSettings] = None


class PersistenceSettings(vars_mod.BaseVarModel):
    save_interval_s: float = 120.0
    max_total_log_bytes: int = 1024 * 1024 * 1024


class InternalHTTPSettings(vars_mod.BaseVarModel):
    host: str = "127.0.0.1"
    port: Optional[int] = None
    secret_key: Optional[str] = None


class MCPSourceScope(str, Enum):
    project = "project"
    workflow = "workflow"


class MCPRootMergeMode(str, Enum):
    replace = "replace"
    append = "append"


class MCPRootEntry(vars_mod.BaseVarModel):
    uri: Optional[str] = None
    path: Optional[str] = None
    name: Optional[str] = None

    @model_validator(mode="after")
    def _normalize(self) -> "MCPRootEntry":
        has_uri = self.uri is not None and self.uri.strip() != ""
        has_path = self.path is not None and self.path.strip() != ""
        if has_uri == has_path:
            raise ValueError("exactly one of uri or path must be provided")
        if has_path:
            raw_path = Path(os.path.expanduser(self.path or ""))
            self.path = str(raw_path)
            self.uri = raw_path.resolve().as_uri()
        if self.uri is None or not self.uri.startswith("file://"):
            raise ValueError("root uri must use the file:// scheme")
        return self


class MCPRootSettings(vars_mod.BaseVarModel):
    entries: List[MCPRootEntry] = Field(default_factory=list)
    list_changed: bool = True
    merge_mode: MCPRootMergeMode = MCPRootMergeMode.replace

    @field_validator("entries", mode="after")
    @classmethod
    def _dedupe_entries(cls, value: List[MCPRootEntry]) -> List[MCPRootEntry]:
        seen: Set[str] = set()
        out: List[MCPRootEntry] = []
        for item in value:
            if item.uri is None or item.uri in seen:
                continue
            seen.add(item.uri)
            out.append(item)
        return out

    @model_validator(mode="after")
    def _validate_merge_mode(self) -> "MCPRootSettings":
        if self.merge_mode == MCPRootMergeMode.append and not self.entries:
            raise ValueError("append root merge_mode requires at least one root entry")
        return self


class MCPProtocolSettings(vars_mod.BaseVarModel):
    request_timeout_s: float = 30.0
    max_request_timeout_s: Optional[float] = 120.0
    startup_timeout_s: float = 15.0
    shutdown_timeout_s: float = 10.0

    @model_validator(mode="after")
    def _validate_timeouts(self) -> "MCPProtocolSettings":
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be greater than 0")
        if self.startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be greater than 0")
        if self.shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be greater than 0")
        if self.max_request_timeout_s is not None:
            if self.max_request_timeout_s <= 0:
                raise ValueError("max_request_timeout_s must be greater than 0")
            if self.max_request_timeout_s < self.request_timeout_s:
                raise ValueError(
                    "max_request_timeout_s must be greater than or equal to request_timeout_s"
                )
        return self


class MCPAuthMode(str, Enum):
    auto = "auto"
    preregistered = "preregistered"
    client_metadata = "client_metadata"
    dynamic = "dynamic"


class MCPAuthSettings(vars_mod.BaseVarModel):
    enabled: bool = True
    mode: MCPAuthMode = MCPAuthMode.auto
    client_id: Optional[str] = None
    client_secret_env: Optional[str] = None
    client_metadata_url: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    redirect_host: str = "127.0.0.1"
    redirect_port: Optional[int] = None
    allow_dynamic_registration: bool = False
    retry_step_up: bool = True
    max_step_up_attempts: int = 2

    @model_validator(mode="after")
    def _validate_auth(self) -> "MCPAuthSettings":
        if self.redirect_port is not None and self.redirect_port <= 0:
            raise ValueError("redirect_port must be greater than 0")
        if self.max_step_up_attempts < 0:
            raise ValueError("max_step_up_attempts must be greater than or equal to 0")
        if self.mode == MCPAuthMode.preregistered and not self.client_id:
            raise ValueError("client_id is required for preregistered auth mode")
        if self.mode == MCPAuthMode.client_metadata and not self.client_metadata_url:
            raise ValueError(
                "client_metadata_url is required for client_metadata auth mode"
            )
        if self.mode == MCPAuthMode.dynamic and not self.allow_dynamic_registration:
            raise ValueError(
                "allow_dynamic_registration must be enabled for dynamic auth mode"
            )
        return self


class MCPStdioSourceSettings(vars_mod.BaseVarModel):
    kind: Literal["stdio"] = "stdio"
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None
    scope: MCPSourceScope = MCPSourceScope.workflow
    roots: Optional[MCPRootSettings] = None


class MCPExternalSourceSettings(vars_mod.BaseVarModel):
    kind: Literal["external"] = "external"
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    scope: MCPSourceScope = MCPSourceScope.project
    roots: Optional[MCPRootSettings] = None
    auth: Optional[MCPAuthSettings] = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value:
            raise ValueError("url must be non-empty")
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return value


MCPSourceSettings = Annotated[
    Union[MCPStdioSourceSettings, MCPExternalSourceSettings],
    Field(discriminator="kind"),
]


class MCPToolSelector(vars_mod.BaseVarModel):
    source: str
    tool: str

    @model_validator(mode="after")
    def _validate_fields(self) -> "MCPToolSelector":
        if not self.source.strip():
            raise ValueError("source must be non-empty")
        if not self.tool.strip():
            raise ValueError("tool must be non-empty")
        return self


class MCPWorkflowSettings(vars_mod.BaseVarModel):
    enabled: bool = True
    tools: List[MCPToolSelector] = Field(default_factory=list)
    disabled_tools: List[MCPToolSelector] = Field(default_factory=list)
    roots: Optional[MCPRootSettings] = None


class MCPSettings(vars_mod.BaseVarModel):
    enabled: bool = True
    sources: Dict[str, MCPSourceSettings] = Field(default_factory=dict)
    roots: Optional[MCPRootSettings] = None
    protocol: Optional[MCPProtocolSettings] = None

    @field_validator("sources", mode="after")
    @classmethod
    def _validate_source_names(
        cls, value: Dict[str, MCPSourceSettings]
    ) -> Dict[str, MCPSourceSettings]:
        for name in value:
            if not name.strip():
                raise ValueError("mcp source names must be non-empty")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError(
                    "mcp source names may contain only letters, digits, dot, underscore, and dash"
                )
        return value


class Settings(vars_mod.BaseVarModel):
    _var_defs: Dict[str, vars_mod.VarDef] = PrivateAttr(default_factory=dict)
    workflows: Dict[str, WorkflowConfig] = Field(default_factory=dict)
    # Optional name of the workflow to auto-start in interactive UIs
    default_workflow: Optional[str] = Field(default=None)
    tools: List[ToolSpec] = Field(default_factory=list)
    # Tool settings
    tool_settings: Optional[ToolSettings] = Field(default=None)
    # Mapping of tool name -> formatter configuration
    tool_call_formatters: Dict[str, ToolCallFormatter] = Field(default_factory=dict)
    know: Optional[KnowProjectSettings] = Field(default=None)
    know_enabled: bool = True
    process: Optional[ProcessSettings] = Field(default=None)
    logging: Optional[LoggingSettings] = Field(default_factory=LoggingSettings)
    persistence: Optional[PersistenceSettings] = Field(default=None)
    tui: Optional[TUIOptions] = Field(default=None)
    internal_http: Optional[InternalHTTPSettings] = Field(default=None)
    mcp: Optional[MCPSettings] = Field(default=None)

    @model_validator(mode="after")
    def _sync_workflow_names(self) -> "Settings":
        for key, wf in self.workflows.items():
            wf.name = key
        return self

    def _set_var_defs(self, defs: Dict[str, vars_mod.VarDef]) -> None:
        self._var_defs = dict(defs)

    _var_bindings: Dict[str, List[vars_mod.VarBinding]] = PrivateAttr(
        default_factory=dict
    )

    def _set_var_bindings(self, bindings: Dict[str, List[vars_mod.VarBinding]]) -> None:
        self._var_bindings = {k: list(v) for k, v in bindings.items()}

    def _apply_var_bindings_for(self, name: str) -> None:
        env = self._var_env
        if env is None:
            return
        bindings = self._var_bindings.get(name)
        if not bindings:
            return
        for b in bindings:
            b.apply(env)

    def list_variables(self) -> Dict[str, vars_mod.VarDef]:
        return dict(self._var_defs)

    def get_variable_def(self, name: str) -> Optional[vars_mod.VarDef]:
        return self._var_defs.get(name)

    def get_variable_value(self, name: str) -> Any:
        env = self._var_env
        if env is None:
            return None
        found, val = env.lookup(name)
        if not found:
            return None
        return val

    def set_variable_value(self, name: str, value: Any) -> None:
        env = self._var_env
        if env is None:
            env = vars_mod.VarEnv({})
            self._var_env = env
        env.vars_map[name] = value
        existing = self._var_defs.get(name)
        if existing is not None:
            existing.value = value
        self._apply_var_bindings_for(name)

    def delete_variable(self, name: str) -> None:
        env = self._var_env
        if env is not None:
            env.vars_map.pop(name, None)
        self._var_defs.pop(name, None)
        self._apply_var_bindings_for(name)

    def list_variable_value_choices(
        self, name: str, needle: str = ""
    ) -> List[vars_values_mod.VarValueChoice]:
        var_def = self._var_defs.get(name)
        if var_def is None:
            return []

        needle_norm = (needle or "").casefold()
        if var_def.options is not None:
            out: List[vars_values_mod.VarValueChoice] = []
            for opt in var_def.options:
                opt_name = opt if isinstance(opt, str) else str(opt)
                if needle_norm and needle_norm not in opt_name.casefold():
                    continue
                out.append(vars_values_mod.VarValueChoice(name=opt_name, value=opt))
            return out

        provider_key = var_def.lookup or var_def.type
        if provider_key is None:
            return []
        return vars_values_mod.list_var_type_values(provider_key, needle)
