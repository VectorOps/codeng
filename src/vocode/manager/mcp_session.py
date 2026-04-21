from __future__ import annotations

import asyncio
from typing import Optional


class ServerMCPAuthenticationSession:
    def __init__(self, operation) -> None:
        self._operation = operation
        self._task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        task = self._task
        if task is None:
            return False
        return not task.done()

    async def run(self) -> object:
        if self.is_active:
            raise RuntimeError("MCP authentication is already in progress.")
        self._task = asyncio.create_task(self._operation())
        try:
            return await self._task
        finally:
            self._task = None

    async def cancel(self) -> bool:
        task = self._task
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True
