from __future__ import annotations

from pathlib import Path

import pytest

from vocode import models
from vocode import project as vocode_project
from vocode.manager import proto as manager_proto
from vocode.tui.app import App
from vocode.tui import uistate as tui_uistate
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_tui_app_sends_user_input_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTUIState:
        def __init__(
            self, on_input, on_autocomplete_request=None, on_stop=None, on_eof=None
        ) -> None:
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
    text = "hello from tui"
    await app.on_input(text)

    envelope = await app._endpoint_server.recv()
    payload = envelope.payload

    assert payload.kind == manager_proto.BasePacketKind.USER_INPUT
    assert isinstance(payload, manager_proto.UserInputPacket)
    assert payload.message.text == text
    assert payload.message.role == models.Role.USER


@pytest.mark.asyncio
async def test_tui_app_clears_prompt_on_input_prompt_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTUIState:
        def __init__(
            self, on_input, on_autocomplete_request=None, on_stop=None, on_eof=None
        ) -> None:
            self._on_input = on_input
            self.last_title: str | None = None
            self.last_subtitle: str | None = None

        def add_markdown(self, markdown: str) -> None:
            return None

        def set_input_panel_title(
            self,
            title: str | None,
            subtitle: str | None = None,
        ) -> None:
            self.last_title = title
            self.last_subtitle = subtitle

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
    state = app._state  # FakeTUIState

    prompt_packet = manager_proto.InputPromptPacket(title=None, subtitle=None)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=prompt_packet)
    await app._handle_packet_input_prompt(envelope)

    assert state.last_title is None
    assert state.last_subtitle is None


@pytest.mark.asyncio
async def test_tui_app_sends_stop_request_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTUIState:
        def __init__(
            self, on_input, on_autocomplete_request=None, on_stop=None, on_eof=None
        ) -> None:
            self._on_input = on_input
            self._on_stop = on_stop

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

    await app.on_stop_request()

    envelope = await app._endpoint_server.recv()
    payload = envelope.payload

    assert payload.kind == manager_proto.BasePacketKind.STOP_REQ
    assert isinstance(payload, manager_proto.StopReqPacket)
