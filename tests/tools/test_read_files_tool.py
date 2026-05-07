from pathlib import Path

import pytest

from vocode import state as vocode_state
from vocode.settings import ToolSpec
from vocode.tools import base as tools_base
from vocode.tools.read_files_tool import ReadFilesTool


class _Project:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path


@pytest.mark.asyncio
async def test_read_files_tool_reads_text_file(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    tool = ReadFilesTool(_Project(tmp_path))
    req = tools_base.ToolReq(
        execution=vocode_state.WorkflowExecution(workflow_name="wf"),
        spec=ToolSpec(name="read_files"),
    )

    response = await tool.run(req, {"path": "hello.txt"})

    assert response is not None
    assert response.text is not None
    assert response.text.startswith("Status: 200 OK\n")
    assert "Content-Type: text/plain; charset=utf-8" in response.text
    assert response.text.endswith("\nhello\n")


@pytest.mark.asyncio
async def test_read_files_tool_blocks_root_gitignored_paths(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("hidden", encoding="utf-8")
    tool = ReadFilesTool(_Project(tmp_path))
    req = tools_base.ToolReq(
        execution=vocode_state.WorkflowExecution(workflow_name="wf"),
        spec=ToolSpec(name="read_files"),
    )

    response = await tool.run(req, {"path": "secret.txt"})

    assert response is not None
    assert (
        response.text == "Status: 403 Forbidden\n\nError: Path is ignored by .gitignore"
    )
