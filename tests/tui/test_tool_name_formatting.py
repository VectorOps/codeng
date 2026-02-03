from __future__ import annotations

from vocode.tui.tcf import base as tcf_base


class _DummyFormatter(tcf_base.BaseToolCallFormatter):
    def format_input(self, terminal, tool_name, arguments, config):
        return None

    def format_output(self, terminal, tool_name, result, config):
        return None


def test_format_tool_name_snake_case() -> None:
    fmt = _DummyFormatter()
    assert fmt.format_tool_name("apply_patch") == "Apply Patch"
    assert fmt.format_tool_name("read_file") == "Read File"


def test_format_tool_name_camel_pascal_case() -> None:
    fmt = _DummyFormatter()
    assert fmt.format_tool_name("ReadFile") == "Read File"
    assert fmt.format_tool_name("readFile") == "Read File"
    assert fmt.format_tool_name("veryComplexToolName123") == "Very Complex Tool Name123"
