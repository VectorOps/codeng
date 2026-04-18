from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from vocode import state


@dataclass
class InputManagerState:
    queued_messages: Deque[state.Message]
    waiters: Deque[asyncio.Future[state.Message]]


class InputManager:
    def __init__(self) -> None:
        self._state = InputManagerState(
            queued_messages=deque(),
            waiters=deque(),
        )
        self._lock = asyncio.Lock()

    def _has_pending_waiters(self) -> bool:
        for waiter in self._state.waiters:
            if not waiter.done():
                return True
        return False

    async def publish(
        self,
        message: state.Message,
        *,
        queue: bool,
    ) -> bool:
        async with self._lock:
            while self._state.waiters:
                waiter = self._state.waiters.popleft()
                if waiter.done():
                    continue
                waiter.set_result(message)
                return True
            if not queue:
                return False
            self._state.queued_messages.append(message)
            return True

    async def wait_for_input(self, only_new: bool = False) -> state.Message:
        waiter: Optional[asyncio.Future[state.Message]] = None
        async with self._lock:
            if not only_new and self._state.queued_messages:
                message = self._state.queued_messages.popleft()
                return message
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self._state.waiters.append(waiter)

        try:
            return await waiter
        finally:
            async with self._lock:
                try:
                    self._state.waiters.remove(waiter)
                except ValueError:
                    pass

    async def snapshot(self) -> InputManagerState:
        async with self._lock:
            return InputManagerState(
                queued_messages=deque(self._state.queued_messages),
                waiters=deque(
                    waiter for waiter in self._state.waiters if not waiter.done()
                ),
            )

    async def dequeue(self) -> Optional[state.Message]:
        async with self._lock:
            if not self._state.queued_messages:
                return None
            return self._state.queued_messages.popleft()

    async def remove_at(self, index: int) -> Optional[state.Message]:
        async with self._lock:
            queue_size = len(self._state.queued_messages)
            if index < 0:
                index = queue_size + index
            if index < 0 or index >= queue_size:
                return None
            message = self._state.queued_messages[index]
            del self._state.queued_messages[index]
            return message

    async def clear_queue(self) -> int:
        async with self._lock:
            count = len(self._state.queued_messages)
            self._state.queued_messages.clear()
            return count

    async def reset(self) -> None:
        async with self._lock:
            self._state.queued_messages.clear()
            for waiter in self._state.waiters:
                if waiter.done():
                    continue
                waiter.cancel()
            self._state.waiters.clear()

    async def reset_all(self) -> None:
        await self.reset()
