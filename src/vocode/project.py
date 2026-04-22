from pathlib import Path
from typing import Optional, Union, Dict, Any, TYPE_CHECKING, List
from asyncio import Queue
import uuid

if TYPE_CHECKING:
    from .tools import BaseTool
    from knowlt.models import Repo

from .scm.git import GitSCM
from .settings import KnowProjectSettings, Settings
from .settings.loader import load_settings
from .templates import write_default_config
from .state import LLMUsageStats
from .input_manager import InputManager
from .know import KnowProject, convert_know_tool
from .proc.manager import ProcessManager
from .proc.base import EnvPolicy
from .proc.shell import ShellManager
from .skills import Skill, discover_skills
from .project_state import FileChangeModel, ProjectState
from .history.manager import HistoryManager
from .auth import ProjectCredentialManager
from vocode.persistence import state_manager as persistence_state_manager
from vocode.http import server as http_server
from vocode.mcp import naming as mcp_naming
from vocode.mcp.service import MCPService
from vocode.tools.mcp_discovery_tool import MCPDiscoveryTool
from vocode.tools.mcp_get_prompt_tool import MCPGetPromptTool
from vocode.tools.mcp_read_resource_tool import MCPReadResourceTool
from vocode.tools.mcp_tool import MCPToolAdapter


class Project:
    def __init__(
        self,
        base_path: Path,
        config_relpath: Path,
        settings: Optional[Settings],
    ):
        self.base_path: Path = base_path
        self.config_relpath: Path = config_relpath
        self.settings: Optional[Settings] = settings
        self.tools: Dict[str, "BaseTool"] = {}
        self.know: KnowProject = KnowProject()
        self.project_state: ProjectState = ProjectState()
        self.input_manager: InputManager = InputManager()
        self.history: HistoryManager = HistoryManager()
        self.credentials: ProjectCredentialManager = ProjectCredentialManager()
        self.llm_usage: LLMUsageStats = LLMUsageStats()
        self.processes: Optional[ProcessManager] = None
        self.shells: Optional[ShellManager] = None
        self.skills: List[Skill] = []
        self.mcp: Optional[MCPService] = None
        self._queue = Queue()
        # Name of the currently running workflow (top-level frame in UIState), if any.
        # Set/cleared by the runner/UI layer; tools may use this for contextual validation.
        self.current_workflow: Optional[str] = None
        self.current_workflow_run_id: Optional[str] = None
        self.last_root_workflow: Optional[str] = None
        self.session_id: str = uuid.uuid4().hex
        save_interval_s = 120.0
        max_total_log_bytes = 1024 * 1024 * 1024
        if self.settings is not None and self.settings.persistence is not None:
            save_interval_s = float(self.settings.persistence.save_interval_s)
            max_total_log_bytes = int(self.settings.persistence.max_total_log_bytes)
        self.state_manager = persistence_state_manager.WorkflowStateManager(
            base_path=self.base_path,
            session_id=self.session_id,
            save_interval_s=save_interval_s,
            max_total_log_bytes=max_total_log_bytes,
        )

    @property
    def config_path(self) -> Path:
        # Do not resolve symlinks; return the composed path as-is
        return self.base_path / self.config_relpath

    @classmethod
    def from_base_path(
        cls,
        base_path: Union[str, Path],
        *,
        search_ancestors: bool = True,
        use_scm: bool = True,
    ) -> "Project":
        return init_project(
            base_path,
            search_ancestors=search_ancestors,
            use_scm=use_scm,
        )

    # Project db
    async def refresh(
        self,
        repo: Optional["Repo"] = None,
        files: Optional[List[FileChangeModel]] = None,
    ) -> None:
        await self.know.refresh(repo)

    # Tool management
    def refresh_tools_from_registry(self) -> None:
        """
        Refresh self.tools from the global registry and dynamic sources, excluding disabled tools per settings.
        """
        from .tools import ToolFactory

        disabled_tool_names = (
            {
                entry.name
                for entry in (self.settings.tools or [])
                if entry.enabled is False
            }
            if self.settings
            else set()
        )

        # Code tools
        all_tools = ToolFactory.all()
        self.tools = {
            name: cls(self)
            for name, cls in all_tools.items()
            if name not in disabled_tool_names and name != MCPDiscoveryTool.name
        }

        if self.settings and self.settings.know_enabled and self.settings.know:
            for t in self.know.pm.get_enabled_tools():
                if t.tool_name not in disabled_tool_names:
                    self.tools[t.tool_name] = convert_know_tool(self, t)

        if self.mcp is not None:
            workflow_mcp = None
            if (
                self.settings is not None
                and self.current_workflow is not None
                and self.current_workflow in self.settings.workflows
            ):
                workflow_mcp = self.settings.workflows[self.current_workflow].mcp
            if workflow_mcp is not None and not workflow_mcp.enabled:
                return
            if (
                self._should_enable_mcp_discovery_tool()
                and MCPDiscoveryTool.name not in disabled_tool_names
            ):
                self.tools[MCPDiscoveryTool.name] = MCPDiscoveryTool(self)
            if (
                self._should_enable_mcp_get_prompt_tool()
                and MCPGetPromptTool.name not in disabled_tool_names
            ):
                self.tools[MCPGetPromptTool.name] = MCPGetPromptTool(self)
            if (
                self._should_enable_mcp_read_resource_tool()
                and MCPReadResourceTool.name not in disabled_tool_names
            ):
                self.tools[MCPReadResourceTool.name] = MCPReadResourceTool(self)
            for source_name, descriptors in self.mcp.list_tool_cache().items():
                for descriptor in descriptors.values():
                    if not self._is_mcp_tool_enabled_for_current_workflow(
                        source_name,
                        descriptor.tool_name,
                    ):
                        continue
                    if self._should_hide_listed_mcp_tools_for_current_workflow():
                        continue
                    internal_name = mcp_naming.build_internal_tool_name(
                        source_name,
                        descriptor.tool_name,
                    )
                    if internal_name in disabled_tool_names:
                        continue
                    self.tools[internal_name] = MCPToolAdapter(
                        self,
                        descriptor,
                        internal_name,
                    )

    def _is_mcp_tool_enabled_for_current_workflow(
        self,
        source_name: str,
        tool_name: str,
    ) -> bool:
        if self.settings is None or self.current_workflow is None or self.mcp is None:
            return False
        workflow = self.settings.workflows.get(self.current_workflow)
        return self.mcp.registry.is_workflow_tool_enabled(
            workflow,
            source_name,
            tool_name,
        )

    def _should_hide_listed_mcp_tools_for_current_workflow(self) -> bool:
        if self.settings is None:
            return False
        hidden = False
        if self.settings.mcp is not None:
            hidden = self.settings.mcp.hide_listed_tools
        if self.current_workflow is None:
            return hidden
        workflow = self.settings.workflows.get(self.current_workflow)
        if workflow is None or workflow.mcp is None:
            return hidden
        return workflow.mcp.hide_listed_tools

    def _should_enable_mcp_discovery_tool(self) -> bool:
        if self.mcp is None or self.settings is None:
            return False
        discovery_settings = None
        if self.settings.mcp is not None:
            discovery_settings = self.settings.mcp.discovery
        if discovery_settings is not None and not discovery_settings.enabled:
            return False
        for source_name, descriptors in self.mcp.list_tool_cache().items():
            if not descriptors:
                continue
            for descriptor in descriptors.values():
                if self._is_mcp_tool_enabled_for_current_workflow(
                    source_name,
                    descriptor.tool_name,
                ):
                    return True
        return False

    def _should_enable_mcp_get_prompt_tool(self) -> bool:
        if not self._has_current_mcp_workflow():
            return False
        return bool(self.mcp is not None and self.mcp.list_prompt_sources())

    def _should_enable_mcp_read_resource_tool(self) -> bool:
        if not self._has_current_mcp_workflow():
            return False
        return bool(self.mcp is not None and self.mcp.list_resource_sources())

    def _has_current_mcp_workflow(self) -> bool:
        if self.mcp is None or self.settings is None or self.current_workflow is None:
            return False
        workflow = self.settings.workflows.get(self.current_workflow)
        return (
            workflow is not None and workflow.mcp is not None and workflow.mcp.enabled
        )

    # LLM usage totals
    def add_llm_usage(
        self, prompt_delta: int, completion_delta: int, cost_delta: float
    ) -> None:
        """Increment aggregate LLM usage totals for this project."""
        stats = self.llm_usage
        stats.prompt_tokens += int(prompt_delta or 0)
        stats.completion_tokens += int(completion_delta or 0)
        stats.cost_dollars += float(cost_delta or 0.0)

    async def on_workflow_started(
        self,
        workflow_name: str,
        workflow_run_id: Optional[str] = None,
    ) -> None:
        self.current_workflow = workflow_name
        self.current_workflow_run_id = workflow_run_id
        if self.mcp is None:
            return
        workflow = None
        if self.settings is not None:
            workflow = self.settings.workflows.get(workflow_name)
        change = await self.mcp.start_workflow(
            workflow_name,
            workflow,
            workflow_run_id=workflow_run_id,
        )
        for source_name in change.started_sources:
            await self.mcp.refresh_tools(source_name)
        if change.started_sources or change.stopped_sources:
            self.refresh_tools_from_registry()

    async def on_workflow_finished(
        self,
        workflow_name: str,
        keep_mcp_sessions: bool = False,
        workflow_run_id: Optional[str] = None,
    ) -> None:
        if self.mcp is None:
            return
        change = await self.mcp.finish_workflow(
            workflow_name,
            keep_mcp_sessions,
            workflow_run_id=workflow_run_id,
        )
        if self.current_workflow_run_id == workflow_run_id:
            self.current_workflow = None
            self.current_workflow_run_id = None
        if change.started_sources or change.stopped_sources:
            self.refresh_tools_from_registry()

    # Lifecycle management
    async def start(self) -> None:
        """
        Start project subsystems that require async initialization (e.g., MCP).
        """
        if self.settings and self.settings.know_enabled and self.settings.know:
            await self.know.start(self.settings.know)
        await self.state_manager.start()

        if self.settings and self.settings.internal_http is not None:
            http_server.configure_internal_http(self.settings.internal_http)

        # Initialize process manager (idempotent)
        if self.processes is None:
            backend_name = (
                self.settings.process.backend
                if (self.settings and self.settings.process)
                else "local"
            )
            env = (
                self.settings.process.env
                if (self.settings and self.settings.process)
                else None
            )
            env_policy = EnvPolicy(
                inherit_parent=(env.inherit_parent if env else True),
                allowlist=(env.allowlist if env else None),
                denylist=(env.denylist if env else None),
                defaults=(env.defaults if env else {}),
            )
            self.processes = ProcessManager(
                backend_name=backend_name,
                default_cwd=self.base_path,
                env_policy=env_policy,
            )

        # Initialize shell manager (idempotent, depends on process manager)
        if self.processes is not None and self.shells is None:
            shell_settings = (
                self.settings.process.shell
                if (self.settings and self.settings.process)
                else None
            )
            self.shells = ShellManager(
                process_manager=self.processes,
                settings=shell_settings,
                default_cwd=self.base_path,
            )

        # Register tools
        self.refresh_tools_from_registry()

        if self.mcp is None:
            mcp_settings = self.settings.mcp if self.settings is not None else None
            has_workflow_roots = False
            has_workflow_roots_list_changed = False
            if self.settings is not None:
                for workflow in self.settings.workflows.values():
                    if workflow.mcp is None or workflow.mcp.roots is None:
                        continue
                    has_workflow_roots = True
                    if workflow.mcp.roots.list_changed:
                        has_workflow_roots_list_changed = True
            self.mcp = MCPService(
                mcp_settings,
                credentials=self.credentials,
                project_root_uri=self.base_path.resolve().as_uri(),
                has_workflow_roots=has_workflow_roots,
                has_workflow_roots_list_changed=has_workflow_roots_list_changed,
            )
        if self.settings and self.settings.mcp and self.settings.mcp.enabled:
            for name, source in self.settings.mcp.sources.items():
                if source.scope.value == "project" and source.kind == "stdio":
                    await self.mcp.start_session(name)

        # Discover skills
        self.skills = discover_skills(self.base_path)

        if self.settings and self.settings.know_enabled and self.settings.know:
            await self.know.refresh_all()

    async def shutdown(self) -> None:
        """Gracefully shut down project components."""
        await self.input_manager.reset_all()
        if self.mcp is not None:
            await self.mcp.close_all()
        # Stop shell manager before underlying processes
        if self.shells is not None:
            await self.shells.stop()
            self.shells = None
        # Stop processes first to release IO and resources
        if self.processes is not None:
            await self.processes.shutdown()
            self.processes = None
        if self.settings and self.settings.know_enabled and self.settings.know:
            await self.know.shutdown()
        await self.state_manager.shutdown()


def _find_project_root_with_config(start: Path, rel_config: Path) -> Optional[Path]:
    """
    Walk upwards from 'start' to filesystem root looking for rel_config (e.g., '.vocode/config.yaml').
    Returns the directory that contains rel_config if found; otherwise None.
    Note: Ignore '.vocode' dirs that don't contain the config file.
    """
    current = start
    while True:
        candidate = current / rel_config
        if candidate.is_file():
            return current
        if current.parent == current:
            # Reached filesystem root
            return None
        current = current.parent


def init_project(
    base_path: Union[str, Path],
    config_relpath: Union[str, Path] = ".vocode/config-ng.yaml",
    *,
    search_ancestors: bool = True,
    use_scm: bool = True,
) -> Project:
    """
    Initialize a Project by:
    1) Searching upwards for an existing .vocode/config.yaml (nearest ancestor) if search_ancestors is True.
    2) Otherwise, if use_scm is True, detecting a Git repository root and using it as the base; create .vocode/config.yaml there if missing.
    3) Otherwise, creating .vocode/config.yaml at the provided start directory.
    """
    start_path = Path(base_path)
    # If a file path is provided, start from its parent; otherwise the directory itself.
    start_dir = start_path if start_path.is_dir() else start_path.parent
    start_dir = start_dir.resolve()

    rel = Path(config_relpath)
    base = None
    config_path = None

    # 1) Search upwards for an existing config file (nearest ancestor)
    found_base = (
        _find_project_root_with_config(start_dir, rel) if search_ancestors else None
    )
    if found_base is not None:
        base = found_base
        config_path = base / rel
    else:
        # 2) Try SCM (git) if enabled
        if use_scm:
            repo_root = GitSCM().find_repo(start_dir)
            if repo_root is not None:
                base = Path(repo_root)
                config_path = base / rel
                if not config_path.exists():
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    write_default_config(config_path)

        # 3) Fall back to the start directory
        if base is None:
            base = start_dir
            config_path = base / rel
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if not config_path.exists():
                write_default_config(config_path)

    # Load merged settings (supports include + YAML/JSON5)
    settings = load_settings(str(config_path))

    if settings.know_enabled:
        if settings.know:
            know_settings = settings.know.model_copy(deep=True)
        else:
            know_settings = KnowProjectSettings(project_name="_", repo_name="_")
            know_settings.project_name = ""
            know_settings.repo_name = ""

        if not know_settings.project_name:
            know_settings.project_name = "my-project"
        if not know_settings.repo_name:
            know_settings.repo_name = base.name
        if not know_settings.repo_path:
            know_settings.repo_path = str(base)

        if not know_settings.repository_connection:
            know_data_path = base / ".vocode/data"
            know_data_path.mkdir(parents=True, exist_ok=True)
            know_settings.repository_connection = str(know_data_path / "know-ng.duckdb")

        settings.know = know_settings

    proj = Project(
        base_path=base,
        config_relpath=rel,
        settings=settings,
    )

    return proj
