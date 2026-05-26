from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from vocode.logger import logger


class FilePathCacheService:
    def __init__(
        self,
        base_path: Path,
        *,
        refresh_interval_s: float = 60.0,
        skip_dirs: Optional[set[str]] = None,
        walker: Callable[[Path, set[str]], list[str]] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._base_path = base_path
        self._refresh_interval_s = refresh_interval_s
        self._skip_dirs = set(skip_dirs or set())
        self._walker = walker or _walk_project_paths
        self._time_fn = time_fn or time.monotonic
        self._condition = threading.Condition()
        self._cached_paths: list[str] = []
        self._last_refresh_at: Optional[float] = None
        self._refresh_generation = 0
        self._refresh_requested = False
        self._refresh_in_progress = False
        self._stopped = False
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="file-path-cache",
            daemon=True,
        )
        self._worker.start()

    async def get_paths(self) -> list[str]:
        return await asyncio.to_thread(self.get_paths_blocking)

    def get_paths_blocking(self) -> list[str]:
        with self._condition:
            refresh_generation = self._refresh_generation
            if self._needs_refresh_locked():
                if not self._refresh_requested and not self._refresh_in_progress:
                    self._refresh_requested = True
                    self._condition.notify_all()
                while self._refresh_generation == refresh_generation:
                    self._condition.wait()
            return list(self._cached_paths)

    def shutdown(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        self._worker.join(timeout=1.0)

    def _needs_refresh_locked(self) -> bool:
        if self._last_refresh_at is None:
            return True
        return (self._time_fn() - self._last_refresh_at) >= self._refresh_interval_s

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._refresh_requested and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                self._refresh_requested = False
                self._refresh_in_progress = True
            try:
                paths = self._walker(self._base_path, self._skip_dirs)
            except Exception as exc:
                logger.exception("file path cache refresh failed", exc=exc)
                paths = None
            with self._condition:
                if paths is not None:
                    self._cached_paths = paths
                self._last_refresh_at = self._time_fn()
                self._refresh_in_progress = False
                self._refresh_generation += 1
                self._condition.notify_all()


def _walk_project_paths(base_path: Path, skip_dirs: set[str]) -> list[str]:
    paths: list[str] = []
    for root, dirnames, filenames in os.walk(base_path):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in skip_dirs]
        root_path = Path(root)
        for dirname in dirnames:
            paths.append((root_path / dirname).relative_to(base_path).as_posix() + "/")
        for filename in filenames:
            paths.append((root_path / filename).relative_to(base_path).as_posix())
    paths.sort()
    return paths
