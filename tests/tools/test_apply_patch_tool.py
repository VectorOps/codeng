import asyncio
from pathlib import Path

import pytest

from vocode.tools import get_tool
from vocode.settings import ToolSpec
from tests.stub_project import StubProject


class PatchTestProject(StubProject):
    def __init__(self, base_path: Path) -> None:
        super().__init__()
        self.base_path = base_path
        self.refresh_calls: list[list[object]] = []

    async def refresh(self, *, files) -> None:
        self.refresh_calls.append(list(files))


@pytest.mark.asyncio
async def test_apply_patch_tool_success(tmp_path: Path):
    # Arrange files
    (tmp_path / "f.txt").write_text("pre\n old\npost\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("remove me", encoding="utf-8")

    patch_text = """*** Begin Patch
*** Update File: f.txt
 pre
- old
+ new
 post
*** Add File: new.txt
+ hello
*** Delete File: gone.txt
*** End Patch"""

    project = PatchTestProject(tmp_path)
    ToolClass = get_tool("apply_patch")
    assert ToolClass is not None, "apply_patch tool should be registered"

    tool = ToolClass(project)  # type: ignore[call-arg]

    # Act: format comes from tool config, not args
    spec = ToolSpec(name="apply_patch", config={"format": "v4a"})
    resp = await tool.run(spec, {"text": patch_text})
    # Allow background refresh task to run
    await asyncio.sleep(0)

    # Assert response summary and filesystem changes
    assert resp is not None
    assert resp.type.value == "text"
    assert resp.text and "Applied patch successfully" in resp.text

    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "pre\n new\npost\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == " hello"
    assert not (tmp_path / "gone.txt").exists()

    # Refresh should be called once with three files
    assert len(project.refresh_calls) == 1
    assert len(project.refresh_calls[0]) == 3


@pytest.mark.asyncio
async def test_apply_patch_tool_unsupported_format(tmp_path: Path):
    project = PatchTestProject(tmp_path)
    ToolClass = get_tool("apply_patch")
    assert ToolClass is not None

    tool = ToolClass(project)  # type: ignore[call-arg]

    # Format provided via tool config; args only include text
    spec = ToolSpec(name="apply_patch", config={"format": "unknown"})
    resp = await tool.run(spec, {"text": "*** Begin Patch\n*** End Patch"})
    assert resp is not None
    assert resp.type.value == "text"
    assert "Unsupported patch format" in (resp.text or "")
