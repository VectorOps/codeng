import asyncio
import json
import os
from pathlib import Path
import pytest

from vocode.proc.manager import ProcessManager
from vocode.tools.exec_tool import ExecTool
from vocode.settings import EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT, ToolSpec
from vocode.settings import ExecToolSettings, Settings, ToolSettings
from tests.stub_project import StubProject

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="POSIX-only tests"),
]
def test_exec_tool_basic_stderr_and_timeout(tmp_path: Path):
    async def scenario():
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        proj = StubProject(process_manager=pm)
        tool = ExecTool(proj)
        # Use a small timeout for CI speed; default is 60s for production use.
        spec = ToolSpec(name="exec", config={"timeout_s": 0.1})

        # Basic echo
        resp1 = await tool.run(spec, {"command": "echo hi"})
        data1 = json.loads(resp1.text or "{}")
        assert data1["timed_out"] is False
        assert data1["exit_code"] == 0
        assert data1["output"] == "hi\n"

        # Non-zero exit code
        resp2 = await tool.run(spec, {"command": "false"})
        data2 = json.loads(resp2.text or "{}")
        assert data2["timed_out"] is False
        assert isinstance(data2["exit_code"], int) and data2["exit_code"] != 0
        assert data2["output"] == ""

        # Combined stdout + stderr
        resp3 = await tool.run(spec, {"command": "echo out; echo err 1>&2"})
        data3 = json.loads(resp3.text or "{}")
        assert "out\n" in data3["output"]
        assert "err\n" in data3["output"]
        assert data3["exit_code"] == 0
        assert data3["timed_out"] is False

        # Timeout handling (fixed timeout in tool)
        resp4 = await tool.run(spec, {"command": "sleep 5"})
        data4 = json.loads(resp4.text or "{}")
        assert data4["timed_out"] is True
        assert data4["exit_code"] is None
        assert data4["output"] == ""

        await pm.shutdown()

    asyncio.run(scenario())


def test_exec_tool_output_truncation(tmp_path: Path):
    async def scenario():
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        proj = StubProject(process_manager=pm)
        tool = ExecTool(proj)
        spec = ToolSpec(name="exec", config={"timeout_s": 1})

        # Generate output larger than the 10KB cap (trailing newline is fine).
        # 12000 characters ensures truncation.
        resp = await tool.run(
            spec, {"command": "python - << 'EOF'\nprint('x' * 12000)\nEOF"}
        )
        data = json.loads(resp.text or "{}")

        assert data["timed_out"] is False
        assert data["exit_code"] == 0
        assert isinstance(data["output"], str)
        assert len(data["output"]) <= EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT

        await pm.shutdown()

    asyncio.run(scenario())


def test_exec_tool_output_truncation_respects_settings(tmp_path: Path):
    async def scenario():
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        # Override project-level max_output_chars to a small value.
        settings = Settings(
            tool_settings=ToolSettings(
                exec_tool=ExecToolSettings(max_output_chars=100),
            ),
        )

        proj = StubProject(settings=settings, process_manager=pm)

        tool = ExecTool(proj)
        spec = ToolSpec(name="exec", config={"timeout_s": 1})

        resp = await tool.run(
            spec, {"command": "python - << 'EOF'\nprint('y' * 1000)\nEOF"}
        )
        data = json.loads(resp.text or "{}")

        assert data["timed_out"] is False
        assert data["exit_code"] == 0
        assert isinstance(data["output"], str)
        assert len(data["output"]) <= 100

        await pm.shutdown()

    asyncio.run(scenario())

