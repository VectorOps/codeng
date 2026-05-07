from __future__ import annotations

import pytest

from vocode import ui_events
from vocode.project import Project


@pytest.mark.asyncio
async def test_publish_ui_event_continues_after_subscriber_error(tmp_path) -> None:
    project = Project(base_path=tmp_path, config_relpath=tmp_path / "x", settings=None)
    received: list[str] = []

    async def failing_handler(_: ui_events.ProjectUIEvent) -> None:
        raise RuntimeError("boom")

    async def ok_handler(event: ui_events.ProjectUIEvent) -> None:
        received.append(event.message)

    project.subscribe_ui_events(failing_handler)
    project.subscribe_ui_events(ok_handler)

    await project.publish_ui_event(ui_events.ProjectUIEvent(message="hello"))

    assert received == ["hello"]
