from typing import Any, Callable, Optional
import asyncio

from knowlt import init_project as know_init_project
from knowlt.models import Repo
from knowlt.project import ProjectManager as KnowProjectManager
from knowlt.settings import ProjectSettings as KnowProjectSettings


class KnowProject:
    """Async wrapper around knowlt.ProjectManager."""

    pm: KnowProjectManager

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
    ) -> None:
        """Asynchronously refresh a repository with optional progress reporting."""
        await self.pm.refresh(
            repo=repo,
        )

    async def refresh_all(self) -> None:
        """Asynchronously refresh all repositories."""
        await self.pm.refresh_all()

    async def maybe_refresh(self) -> None:
        """Asynchronously refresh if cooldown has passed."""
        await self.pm.maybe_refresh()
