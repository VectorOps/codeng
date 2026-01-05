from __future__ import annotations

from pathlib import Path

import pytest

from vocode import models
from vocode import project as vocode_project
from vocode.manager import proto as manager_proto
from vocode.tui.app import App, PromptMeta
from vocode.tui import uistate as tui_uistate
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_tui_app_sends_user_input_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTUIState:
        def __init__(self, on_input) -> None:
            self._on_input = on_input
        
        def add_markdown(self, markdown: str) -> None:
            return None

        def set_input_panel_title(
            self,
            title: str | None,
            subtitle: str | None = None,
        ) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(tui_uistate, "TUIState", FakeTUIState)

    monkeypatch.setattr(
        vocode_project.Project,
        "from_base_path",
        classmethod(lambda cls, path: StubProject()),
    )

    app = App(project_path=tmp_path)
    app._push_prompt(PromptMeta(title="test"))
    text = "hello from tui"
    await app.on_input(text)

    envelope = await app._endpoint_server.recv()
    payload = envelope.payload

    assert payload.kind == manager_proto.BasePacketKind.USER_INPUT
    assert isinstance(payload, manager_proto.UserInputPacket)
    assert payload.message.text == text
    assert payload.message.role == models.Role.USER