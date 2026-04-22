from __future__ import annotations

import io
import types

from rich import console as rich_console

from vocode import settings as vocode_settings
from vocode import state as vocode_state
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.tcf import generic as generic_tcf


def test_generic_formatter_hides_response_by_default() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = generic_tcf.GenericToolCallFormatter()

    rendered = formatter.render_response(
        terminal=term,
        resp=types.SimpleNamespace(
            id="call_1",
            name="list_files",
            result={"items": ["a.txt"]},
        ),
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        config=vocode_settings.ToolCallFormatter(title="List Files"),
    )

    assert rendered is None


def test_generic_formatter_renders_response_body_without_arrow_when_enabled() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = generic_tcf.GenericToolCallFormatter()

    rendered = formatter.render_response(
        terminal=term,
        resp=vocode_state.ToolCallResp(
            id="call_1",
            name="list_files",
            status=vocode_state.ToolCallStatus.COMPLETED,
            result={"items": ["a.txt"]},
        ),
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        config=vocode_settings.ToolCallFormatter(
            title="List Files",
            show_output=True,
        ),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert '"items": ["a.txt"]' in output
    assert "=>" not in output
