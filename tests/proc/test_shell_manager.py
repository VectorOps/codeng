import asyncio
import os
from pathlib import Path

import pytest

from vocode.proc.manager import ProcessManager
from vocode.proc.shell import ShellManager, ShellCommandHandle
from vocode.settings import ShellSettings, ShellMode

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only tests")


async def _read_all_stdout(cmd: ShellCommandHandle) -> str:
    chunks: list[str] = []
    async for line in cmd.iter_stdout():
        chunks.append(line)
    return "".join(chunks)


def test_shell_manager_direct_mode_runs_command(tmp_path: Path) -> None:
    async def scenario() -> None:
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        settings = ShellSettings(mode=ShellMode.direct)
        manager = ShellManager(
            process_manager=pm,
            settings=settings,
            default_cwd=tmp_path,
        )

        cmd = await manager.run("printf '%s\n' \"HELLO\"")
        out = await _read_all_stdout(cmd)
        rc = await cmd.wait()

        assert out == "HELLO\n"
        assert rc == 0
        assert cmd.returncode == 0

        await manager.stop()
        await pm.shutdown()

    asyncio.run(scenario())


def test_shell_manager_accepts_timeout_argument(tmp_path: Path) -> None:
    async def scenario() -> None:
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        settings = ShellSettings(mode=ShellMode.direct)
        manager = ShellManager(
            process_manager=pm,
            settings=settings,
            default_cwd=tmp_path,
        )

        # Fast command; timeout should not trigger, but the call should succeed.
        cmd = await manager.run("echo 'OK'", timeout=5.0)
        out = await _read_all_stdout(cmd)
        rc = await cmd.wait()

        assert out.strip() == "OK"
        assert rc == 0

        await manager.stop()
        await pm.shutdown()

    asyncio.run(scenario())


def test_shell_manager_shell_mode_reuses_long_lived_shell_and_strips_marker(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        settings = ShellSettings(mode=ShellMode.shell)
        manager = ShellManager(
            process_manager=pm,
            settings=settings,
            default_cwd=tmp_path,
        )

        cmd1 = await manager.run("printf 'ONE\n'")
        out1 = await _read_all_stdout(cmd1)
        rc1 = await cmd1.wait()

        assert out1 == "ONE\n"
        assert rc1 == 0
        assert "VOCODE_MARK_" not in out1
        pid1 = cmd1.pid
        assert pid1 is not None

        cmd2 = await manager.run("printf 'TWO\n'")
        out2 = await _read_all_stdout(cmd2)
        rc2 = await cmd2.wait()

        assert out2 == "TWO\n"
        assert rc2 == 0
        assert "VOCODE_MARK_" not in out2
        pid2 = cmd2.pid
        assert pid2 == pid1

        # Non-zero exit code should be propagated via marker parsing.
        cmd3 = await manager.run("false")
        _ = await _read_all_stdout(cmd3)
        rc3 = await cmd3.wait()
        assert rc3 == 1
        assert cmd3.returncode == 1

        await manager.stop()
        await pm.shutdown()

    asyncio.run(scenario())
