from __future__ import annotations

import io
import types

from rich import console as rich_console

from vocode import settings as vocode_settings
from vocode import state as vocode_state
from vocode.tui import tcf as tui_tcf
from vocode.tui.tcf import apply_patch
from vocode.tui.lib import terminal as tui_terminal


def test_apply_patch_formatter_renders_markdown_input() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = apply_patch.ApplyPatchToolCallFormatter()

    # Input with markdown
    text_content = "# Title\n- Item 1\n- Item 2"
    arguments = {"text": text_content}

    rendered = formatter.render(
        terminal=term,
        req=vocode_state.ToolCallReq(
            id="call_1",
            name="apply_patch",
            arguments=arguments,
        ),
        resp=None,
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        config=vocode_settings.ToolCallFormatter(title="Apply Patch"),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Apply Patch" in output
    assert "Title" in output
    assert "Item 1" in output


def test_apply_patch_formatter_renders_output_unstyled() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = apply_patch.ApplyPatchToolCallFormatter()

    result = "\n".join(
        [
            "Applied patch successfully.",
            "Added file: foo.py",
            "Updated file: bar.py",
            "Deleted file: baz.py",
            "Renamed file: qux.py",
            "Extra line that should be hidden",
        ]
    )

    rendered = formatter.render(
        terminal=term,
        req=None,
        resp=types.SimpleNamespace(
            id="call_1",
            name="apply_patch",
            result=result,
        ),
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        # config with show_output=False to verify we ignore it
        config=vocode_settings.ToolCallFormatter(
            title="Apply Patch", show_output=False
        ),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Apply Patch" in output
    assert "Applied patch successfully." in output
    assert "Added file: foo.py" in output
    assert "Deleted file: baz.py" in output
    assert "Renamed file: qux.py" in output
    assert "Extra line that should be hidden" in output


def test_apply_patch_formatter_renders_output_from_json_payload() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = apply_patch.ApplyPatchToolCallFormatter()

    # Simulates tool result being a dict that contains a JSON-encoded string.
    result = {"text": '{"message": "Applied patch successfully.\\nAdded file: foo.py"}'}

    rendered = formatter.render(
        terminal=term,
        req=None,
        resp=types.SimpleNamespace(
            id="call_1",
            name="apply_patch",
            result=result,
        ),
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        config=vocode_settings.ToolCallFormatter(title="Apply Patch"),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Applied patch successfully." in output
    assert "Added file: foo.py" in output


def test_apply_patch_formatter_renders_error_from_json_payload() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = apply_patch.ApplyPatchToolCallFormatter()

    # Simulates tool error coming as a JSON string.
    result = '{"error": "Patch failed"}'

    rendered = formatter.render(
        terminal=term,
        req=None,
        resp=types.SimpleNamespace(
            id="call_1",
            name="apply_patch",
            result=result,
        ),
        context=tui_tcf.ToolCallRenderContext(max_width=term.console.size.width),
        config=vocode_settings.ToolCallFormatter(title="Apply Patch"),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Patch failed" in output
