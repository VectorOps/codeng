from typing import Any, Callable, Optional
import asyncio

from knowlt import init_project as know_init_project
from knowlt.models import Repo
from knowlt.project import ProjectManager as KnowProjectManager
from knowlt.settings import ProjectSettings as KnowProjectSettings


class KnowProject:
    """Async wrapper around knowlt.ProjectManager."""

    pm: KnowProjectManager

    def __init__(self) -> None:
        self.default_progress_callback: Optional[Callable[[Any], None]] = None

    async def start(self, settings: KnowProjectSettings) -> None:
        """Initialize the ProjectManager (no auto-refresh)."""
        self.pm = await know_init_project(settings, refresh=False)

    async def shutdown(self) -> None:
        """Shut down the project manager."""
        await self.pm.destroy()

    @property
    def data(self):
        """Direct access to the data repository."""
        return self.pm.data

    async def refresh(
        self,
        repo: Optional[Repo] = None,
        progress_callback: Optional[Callable[[Any], None]] = None,
    ) -> None:
        """Asynchronously refresh a repository with optional progress reporting."""
        if progress_callback is None:
            progress_callback = self.default_progress_callback
        await self.pm.refresh(
            repo=repo,
            progress_callback=progress_callback,
        )

    async def refresh_all(
        self,
        progress_callback: Optional[Callable[[Any], None]] = None,
    ) -> None:
        """Asynchronously refresh all repositories."""
        if progress_callback is None:
            progress_callback = self.default_progress_callback
        await self.pm.refresh_all(progress_callback=progress_callback)

    async def maybe_refresh(self) -> None:
        """Asynchronously refresh if cooldown has passed."""
        await self.pm.maybe_refresh()
