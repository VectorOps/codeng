from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime
from pathlib import Path
from typing import Optional
import shutil
import uuid
import re

from vocode import state
from vocode.logger import logger
from vocode.persistence import codec as persistence_codec


WorkflowChangedListener = Callable[[uuid.UUID], None]


class NullWorkflowStateManager:
    def subscribe(self, listener: WorkflowChangedListener) -> None:
        return None

    def track(self, execution: state.WorkflowExecution) -> None:
        return None

    def notify_changed(self, execution: state.WorkflowExecution) -> None:
        return None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class WorkflowStateManager:
    def __init__(
        self,
        *,
        base_path: Path,
        session_id: str,
        save_interval_s: float = 120.0,
        max_total_log_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self._base_path = base_path
        self._session_id = session_id
        self._save_interval_s = float(save_interval_s)
        self._max_total_log_bytes = int(max_total_log_bytes)
        self._date_prefix = datetime.datetime.now().strftime("%Y_%m_%d")
        self._session_dir_name: Optional[str] = None
        self._executions: dict[uuid.UUID, state.WorkflowExecution] = {}
        self._dirty: set[uuid.UUID] = set()
        self._listeners: list[WorkflowChangedListener] = []
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def sessions_root(self) -> Path:
        return self._base_path / ".vocode" / "sessions"

    @property
    def session_dir(self) -> Path:
        if self._session_dir_name is None:
            self._session_dir_name = self._compute_session_dir_name()
        return self.sessions_root / self._session_dir_name

    def _compute_session_dir_name(self) -> str:
        root = self.sessions_root
        if not root.exists():
            seq = 1
        else:
            prefix = f"{self._date_prefix}_"
            highest = 0
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                name = d.name
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix) :]
                if "_" not in rest:
                    continue
                seq_str, _ = rest.split("_", 1)
                if not re.fullmatch(r"\d+", seq_str):
                    continue
                highest = max(highest, int(seq_str))
            seq = highest + 1
        return f"{self._date_prefix}_{seq}_{self._session_id}"

    def _session_size_bytes(self, session_dir: Path) -> int:
        total = 0
        for p in session_dir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
        return total

    def _enforce_retention(self) -> None:
        if self._max_total_log_bytes <= 0:
            return
        root = self.sessions_root
        if not root.exists():
            return

        sessions: list[tuple[float, str, Path, int]] = []
        total = 0
        for d in root.iterdir():
            if not d.is_dir():
                continue
            try:
                mtime = d.stat().st_mtime
            except OSError:
                continue
            size = self._session_size_bytes(d)
            total += size
            sessions.append((mtime, d.name, d, size))

        if total <= self._max_total_log_bytes:
            return

        candidates = [s for s in sessions if s[1] != (self._session_dir_name or "")]
        candidates.sort(key=lambda x: x[0])
        for _, session_id, d, size in candidates:
            if total <= self._max_total_log_bytes:
                return
            try:
                shutil.rmtree(d)
                total -= size
            except Exception as exc:
                logger.exception(
                    "WorkflowStateManager failed to delete old session",
                    exc=exc,
                    session_id=session_id,
                    session_dir=str(d),
                )

        if total > self._max_total_log_bytes:
            logger.warning(
                "WorkflowStateManager log retention limit exceeded",
                total_bytes=total,
                limit_bytes=self._max_total_log_bytes,
                current_session_id=self._session_id,
            )

    def subscribe(self, listener: WorkflowChangedListener) -> None:
        self._listeners.append(listener)

    def track(self, execution: state.WorkflowExecution) -> None:
        self._executions[execution.id] = execution

    def notify_changed(self, execution: state.WorkflowExecution) -> None:
        self._executions[execution.id] = execution
        self._dirty.add(execution.id)
        for listener in list(self._listeners):
            try:
                listener(execution.id)
            except Exception as exc:
                logger.exception("WorkflowStateManager listener exception", exc=exc)

    def _path_for(self, execution_id: uuid.UUID) -> Path:
        return self.session_dir / f"{execution_id}.json.gz"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._session_dir_name is None:
            self._session_dir_name = await asyncio.to_thread(self._compute_session_dir_name)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._enforce_retention)
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self.flush_all()

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._save_interval_s)
                await self.flush_dirty()
        except asyncio.CancelledError:
            return

    async def flush_dirty(self) -> None:
        ids = list(self._dirty)
        if not ids:
            return
        self._dirty.difference_update(ids)
        await self._flush_ids(ids)

    async def flush_all(self) -> None:
        ids = list(self._executions.keys())
        self._dirty.clear()
        await self._flush_ids(ids)

    async def _flush_ids(self, ids: list[uuid.UUID]) -> None:
        tasks: list[asyncio.Future[None]] = []
        for execution_id in ids:
            execution = self._executions.get(execution_id)
            if execution is None:
                continue
            path = self._path_for(execution_id)
            tasks.append(
                asyncio.to_thread(persistence_codec.save_to_path, path, execution)
            )
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.to_thread(self._enforce_retention)
