from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol
from typing import runtime_checkable


@runtime_checkable
class ShellCommandHandle(Protocol):
    """
    Represents a single shell command execution, mirroring ProcessHandle/LocalProcessHandle.
    """

    id: str
    name: Optional[str]

    @property
    def pid(self) -> Optional[int]:
        ...

    @property
    def returncode(self) -> Optional[int]:
        ...

    def alive(self) -> bool:
        ...

    async def write(self, data: str | bytes) -> None:
        ...

    async def close_stdin(self) -> None:
        ...

    async def iter_stdout(self) -> AsyncIterator[str]:
        ...

    async def iter_stderr(self) -> AsyncIterator[str]:
        ...

    async def terminate(self, grace_s: float = 5.0) -> None:
        ...

    async def kill(self) -> None:
        ...

    async def wait(self) -> int:
        ...


class ShellProcessor(Protocol):
    """
    Processor abstraction used by ShellManager for different execution modes.
    """

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def run(self, command: str) -> ShellCommandHandle:
        ...