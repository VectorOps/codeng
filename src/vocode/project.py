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
from .know import KnowProject, convert_know_tool
from .proc.manager import ProcessManager
from .proc.base import EnvPolicy
from .proc.shell import ShellManager
from .skills import Skill, discover_skills
from .project_state import FileChangeModel, ProjectState
from vocode.persistence import state_manager as persistence_state_manager
from vocode.http import server as http_server


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
        self.llm_usage: LLMUsageStats = LLMUsageStats()
        self.processes: Optional[ProcessManager] = None
        self.shells: Optional[ShellManager] = None
        self.skills: List[Skill] = []
        self._queue = Queue()
        # Name of the currently running workflow (top-level frame in UIState), if any.
        # Set/cleared by the runner/UI layer; tools may use this for contextual validation.
        self.current_workflow: Optional[str] = None
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
            if name not in disabled_tool_names
        }

        if self.settings and self.settings.know_enabled and self.settings.know:
            for t in self.know.pm.get_enabled_tools():
                if t.tool_name not in disabled_tool_names:
                    self.tools[t.tool_name] = convert_know_tool(self, t)

    # LLM usage totals
    def add_llm_usage(
        self, prompt_delta: int, completion_delta: int, cost_delta: float
    ) -> None:
        """Increment aggregate LLM usage totals for this project."""
        stats = self.llm_usage
        stats.prompt_tokens += int(prompt_delta or 0)
        stats.completion_tokens += int(completion_delta or 0)
        stats.cost_dollars += float(cost_delta or 0.0)

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

        # Discover skills
        self.skills = discover_skills(self.base_path)

        if self.settings and self.settings.know_enabled and self.settings.know:
            await self.know.refresh_all()

    async def shutdown(self) -> None:
        """Gracefully shut down project components."""
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
