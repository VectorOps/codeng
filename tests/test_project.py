from __future__ import annotations

from pathlib import Path

import pytest

from vocode.project import Project
from vocode.settings import Settings


class _DummyKnowPM:
    def get_enabled_tools(self):
        return []


class _DummyKnowProject:
    def __init__(self):
        self.pm = _DummyKnowPM()

    async def start(self, settings):
        return None

    async def shutdown(self):
        return None

    async def refresh_all(self):
        return None


@pytest.mark.asyncio
async def test_project_start_initializes_subsystems_and_tools(tmp_path):
    settings = Settings()
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()

    assert project.processes is not None
    assert project.shells is not None
    assert isinstance(project.tools, dict)
    assert "exec" in project.tools

    await project.shutdown()