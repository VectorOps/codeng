from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional, Dict, Protocol, runtime_checkable, Callable


@dataclass
class EnvPolicy:
    inherit_parent: bool = True
    allowlist: Optional[list[str]] = None
    denylist: Optional[list[str]] = None
    defaults: Dict[str, str] = field(default_factory=dict)


@dataclass
class SpawnOptions:
    command: str
    name: Optional[str] = None
    cwd: Optional[Path] = None
    env_overlay: Optional[Dict[str, str]] = None
    shell: bool = True
    # Reserved for future PTY support
    use_pty: bool = False
    # When True, the subprocess should be placed into its own process group
    # so that terminate()/kill() can affect the whole tree. Defaults to True
    # for safer shutdown semantics.
    use_process_group: bool = True


@runtime_checkable
class ProcessHandle(Protocol):
    id: str
    name: Optional[str]

    @property
    def pid(self) -> Optional[int]: ...
    @property
    def returncode(self) -> Optional[int]: ...
    def alive(self) -> bool: ...

    async def write(self, data: str | bytes) -> None: ...
    async def close_stdin(self) -> None: ...
    async def iter_stdout(self) -> AsyncIterator[str]: ...
    async def iter_stderr(self) -> AsyncIterator[str]: ...
    async def terminate(self, grace_s: float = 5.0) -> None: ...
    async def kill(self) -> None: ...
    async def wait(self) -> int: ...


class ProcessBackend(Protocol):
    # Public property (no getattr/hasattr needed)
    env_policy: EnvPolicy

    async def spawn(self, opts: SpawnOptions) -> ProcessHandle: ...


# Backend registry
_BACKENDS: dict[str, Callable[[], ProcessBackend]] = {}


def register_backend(name: str, factory: Callable[[], ProcessBackend]) -> None:
    _BACKENDS[name] = factory


def get_backend(name: str) -> ProcessBackend:
    if name not in _BACKENDS:
        raise ValueError(f"Unknown process backend: {name!r}")
    return _BACKENDS[name]()
