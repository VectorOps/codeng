from __future__ import annotations

import asyncio
import sys

import pytest

from vocode.mcp.process_manager import MCPStdioProcessManager


@pytest.mark.asyncio
async def test_process_manager_starts_and_closes_process() -> None:
    manager = MCPStdioProcessManager(
        sys.executable,
        args=["-c", "import sys; [None for _ in sys.stdin]"],
    )

    proc = await manager.start()

    assert proc.returncode is None
    assert manager.is_running is True

    await manager.close()

    assert manager.is_running is False


@pytest.mark.asyncio
async def test_process_manager_close_is_idempotent() -> None:
    manager = MCPStdioProcessManager(
        sys.executable,
        args=["-c", "import sys; [None for _ in sys.stdin]"],
    )

    await manager.start()
    await manager.close()
    await manager.close()

    assert manager.is_running is False


@pytest.mark.asyncio
async def test_process_manager_start_returns_same_running_process() -> None:
    manager = MCPStdioProcessManager(
        sys.executable,
        args=["-c", "import asyncio; asyncio.run(asyncio.sleep(1))"],
    )

    proc1 = await manager.start()
    proc2 = await manager.start()

    assert proc1 is proc2

    await manager.close()


@pytest.mark.asyncio
async def test_process_manager_terminates_hung_process() -> None:
    manager = MCPStdioProcessManager(
        sys.executable,
        args=[
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ],
        shutdown_timeout_s=0.05,
    )

    await manager.start()
    started = asyncio.get_running_loop().time()
    await manager.close()
    elapsed = asyncio.get_running_loop().time() - started

    assert manager.is_running is False
    assert elapsed < 1.0
