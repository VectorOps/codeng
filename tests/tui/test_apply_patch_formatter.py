from __future__ import annotations

import io

from rich import console as rich_console

from vocode import settings as vocode_settings
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

    rendered = formatter.format_input(
        terminal=term,
        tool_name="apply_patch",
        arguments=arguments,
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

    result = "Applied patch successfully.\nAdded file: foo.py"

    rendered = formatter.format_output(
        terminal=term,
        tool_name="apply_patch",
        result=result,
        # config with show_output=False to verify we ignore it
        config=vocode_settings.ToolCallFormatter(
            title="Apply Patch", show_output=False
        ),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Apply Patch" in output
    assert "=>" in output
    assert "Applied patch successfully." in output
    assert "Added file: foo.py" in output


def test_apply_patch_formatter_renders_output_from_json_payload() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = apply_patch.ApplyPatchToolCallFormatter()

    # Simulates tool result being a dict that contains a JSON-encoded string.
    result = {"text": '{"message": "Applied patch successfully.\\nAdded file: foo.py"}'}

    rendered = formatter.format_output(
        terminal=term,
        tool_name="apply_patch",
        result=result,
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

    rendered = formatter.format_output(
        terminal=term,
        tool_name="apply_patch",
        result=result,
        config=vocode_settings.ToolCallFormatter(title="Apply Patch"),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()

    assert "Patch failed" in output
