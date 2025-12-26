from pathlib import Path
from typing import Optional, Union, Dict, Any, TYPE_CHECKING, List
from enum import Enum
from pydantic import BaseModel
from asyncio import Queue

if TYPE_CHECKING:
    from .tools import BaseTool
    from knowlt.models import Repo

from .scm.git import GitSCM
from .settings import KnowProjectSettings, Settings
from .settings.loader import load_settings
from .templates import write_default_config
from .state import LLMUsageStats
from .know import KnowProject


class ProjectState:
    """
    Ephemeral, process-local project-level state shared across executors.
    Not persisted across runs.
    """

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class FileChangeType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class FileChangeModel(BaseModel):
    type: FileChangeType
    # Relative filename within the project root
    relative_filename: str


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
        self.know: KnowProject = KnowProject()
        # Project-level shared state for executors
        self.project_state: ProjectState = ProjectState()
        # Ephemeral (per-process) global LLM usage totals
        self.llm_usage: LLMUsageStats = LLMUsageStats()
        # Name of the currently running workflow (top-level frame in UIState), if any.
        # Set/cleared by the runner/UI layer; tools may use this for contextual validation.
        self.current_workflow: Optional[str] = None
        # Message queue
        self._queue = Queue()

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

    async def shutdown(self) -> None:
        """Gracefully shut down project components."""
        pass

    async def refresh(
        self,
        repo: Optional["Repo"] = None,
        files: Optional[List[FileChangeModel]] = None,
    ) -> None:
        pass

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
        # Initialize knowlt manager before subsystems that might depend on it.
        if self.settings and self.settings.know:
            await self.know.start(self.settings.know)

        # Perform an initial refresh of all 'know' repositories on start
        await self.know.refresh_all(progress_sender=self.send_message)


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
    config_relpath: Union[str, Path] = ".vocode/config.yaml",
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

    # Initialize `know` project.
    if settings.know:
        # Create a mutable copy of know settings to populate defaults.
        know_settings = settings.know.model_copy(deep=True)
    else:
        # Create default settings if 'know' section is missing from config.
        # Required fields are given placeholder values that will be immediately
        # cleared to trigger the defaulting logic below.
        know_settings = KnowProjectSettings(project_name="_", repo_name="_")
        know_settings.project_name = ""
        know_settings.repo_name = ""

    # Default project/repo names if not set.
    if not know_settings.project_name:
        know_settings.project_name = "my-project"
    if not know_settings.repo_name:
        know_settings.repo_name = base.name
    if not know_settings.repo_path:
        know_settings.repo_path = str(base)

    # Default database path.
    if not know_settings.repository_connection:
        know_data_path = base / ".vocode/data"
        know_data_path.mkdir(parents=True, exist_ok=True)
        know_settings.repository_connection = str(know_data_path / "know.duckdb")

    # Persist computed know settings for deferred async initialization in start()
    settings.know = know_settings

    proj = Project(
        base_path=base,
        config_relpath=rel,
        settings=settings,
    )

    return proj
