from __future__ import annotations

import asyncio

import pytest

from vocode.manager import proto as manager_proto
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib.input import base as input_base


@pytest.mark.asyncio
async def test_tui_progress_gate_shows_after_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    monkeypatch.setattr(tui_uistate, "PROGRESS_VISIBILITY_DELAY_S", 0.0)

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    pkt = manager_proto.ProgressPacket(
        progress_id="p1",
        status=manager_proto.ProgressStatus.START,
        title="Work",
        message=None,
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.SPINNER,
    )
    ui_state.handle_progress(pkt)
    await asyncio.sleep(0.01)
    assert "p1" in ui_state._progress_visible_by_id
    assert ui_state._progress_component is not None

    ui_state.handle_progress(
        manager_proto.ProgressPacket(
            progress_id="p1",
            status=manager_proto.ProgressStatus.END,
        )
    )
    assert "p1" not in ui_state._progress_visible_by_id
    assert ui_state._progress_component is None
