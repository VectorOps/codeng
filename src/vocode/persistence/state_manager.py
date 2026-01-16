from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Optional
import uuid

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
    ) -> None:
        self._base_path = base_path
        self._session_id = session_id
        self._save_interval_s = float(save_interval_s)
        self._executions: dict[uuid.UUID, state.WorkflowExecution] = {}
        self._dirty: set[uuid.UUID] = set()
        self._listeners: list[WorkflowChangedListener] = []
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def session_dir(self) -> Path:
        return self._base_path / ".vocode" / "sessions" / self._session_id

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
        self.session_dir.mkdir(parents=True, exist_ok=True)
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
