from vocode import ui_events
from vocode.tui import uistate as tui_uistate


def test_format_ui_event_markup_includes_source_and_details() -> None:
    markup = tui_uistate.format_ui_event_markup(
        ui_events.ProjectUIEvent(
            severity=ui_events.UIEventSeverity.ERROR,
            title="MCP source start failed",
            source="broken",
            message="MCP source 'broken' failed to start: boom",
            details="unexpected notification received before initialize response",
        )
    )

    assert "MCP source start failed" in markup
    assert "broken" in markup
    assert "failed to start: boom" in markup
    assert "unexpected notification received before initialize response" in markup
