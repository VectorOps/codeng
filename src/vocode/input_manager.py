from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, Optional

from vocode import state


INPUT_TYPE_INTERACTIVE = "interactive"
INPUT_TYPE_HTTP = "http"


def normalize_input_type(input_type: Optional[str]) -> str:
    if input_type is None:
        return INPUT_TYPE_INTERACTIVE
    normalized = input_type.strip()
    if not normalized:
        return INPUT_TYPE_INTERACTIVE
    return normalized


def ordered_input_types(input_types: Iterable[str]) -> list[str]:
    unique_types = {normalize_input_type(input_type) for input_type in input_types}
    ordered: list[str] = []
    for input_type in (INPUT_TYPE_INTERACTIVE, INPUT_TYPE_HTTP):
        if input_type in unique_types:
            ordered.append(input_type)
            unique_types.remove(input_type)
    ordered.extend(sorted(unique_types))
    return ordered


@dataclass
class InputQueueState:
    queued_messages: Deque[state.Message]
    waiters: Deque[asyncio.Future[state.Message]]


@dataclass
class InputManagerState:
    queued_messages: Deque[state.Message]
    waiters: Deque[asyncio.Future[state.Message]]
    queued_messages_by_type: Dict[str, Deque[state.Message]]
    waiters_by_type: Dict[str, Deque[asyncio.Future[state.Message]]]


class InputManager:
    def __init__(self) -> None:
        self._state_by_type: Dict[str, InputQueueState] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_state(self, input_type: str) -> InputQueueState:
        queue_state = self._state_by_type.get(input_type)
        if queue_state is None:
            queue_state = InputQueueState(
                queued_messages=deque(),
                waiters=deque(),
            )
            self._state_by_type[input_type] = queue_state
        return queue_state

    def _prune_done_waiters(self, queue_state: InputQueueState) -> None:
        while queue_state.waiters and queue_state.waiters[0].done():
            queue_state.waiters.popleft()

    def _snapshot_states(self) -> Dict[str, InputQueueState]:
        snapshot: Dict[str, InputQueueState] = {}
        for input_type in ordered_input_types(self._state_by_type.keys()):
            queue_state = self._state_by_type[input_type]
            waiters = deque(
                waiter for waiter in queue_state.waiters if not waiter.done()
            )
            if not queue_state.queued_messages and not waiters:
                continue
            snapshot[input_type] = InputQueueState(
                queued_messages=deque(queue_state.queued_messages),
                waiters=waiters,
            )
        return snapshot

    def _has_pending_waiters(self) -> bool:
        for queue_state in self._state_by_type.values():
            for waiter in queue_state.waiters:
                if not waiter.done():
                    return True
        return False

    async def publish(
        self,
        message: state.Message,
        *,
        queue: bool,
        input_type: Optional[str] = None,
    ) -> bool:
        resolved_input_type = normalize_input_type(input_type)
        async with self._lock:
            queue_state = self._get_or_create_state(resolved_input_type)
            self._prune_done_waiters(queue_state)
            while queue_state.waiters:
                waiter = queue_state.waiters.popleft()
                if waiter.done():
                    continue
                waiter.set_result(message)
                return True
            if not queue:
                return False
            queue_state.queued_messages.append(message)
            return True

    async def wait_for_input(
        self,
        only_new: bool = False,
        input_type: Optional[str] = None,
    ) -> state.Message:
        resolved_input_type = normalize_input_type(input_type)
        waiter: Optional[asyncio.Future[state.Message]] = None
        async with self._lock:
            queue_state = self._get_or_create_state(resolved_input_type)
            self._prune_done_waiters(queue_state)
            if not only_new and queue_state.queued_messages:
                message = queue_state.queued_messages.popleft()
                return message
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            queue_state.waiters.append(waiter)

        try:
            return await waiter
        finally:
            async with self._lock:
                queue_state = self._state_by_type.get(resolved_input_type)
                if queue_state is None:
                    return
                try:
                    queue_state.waiters.remove(waiter)
                except ValueError:
                    pass

    async def snapshot(self) -> InputManagerState:
        async with self._lock:
            state_by_type = self._snapshot_states()
            queued_messages: Deque[state.Message] = deque()
            waiters: Deque[asyncio.Future[state.Message]] = deque()
            for input_type in ordered_input_types(state_by_type.keys()):
                queue_state = state_by_type[input_type]
                queued_messages.extend(queue_state.queued_messages)
                waiters.extend(queue_state.waiters)
            return InputManagerState(
                queued_messages=queued_messages,
                waiters=waiters,
                queued_messages_by_type={
                    input_type: deque(queue_state.queued_messages)
                    for input_type, queue_state in state_by_type.items()
                },
                waiters_by_type={
                    input_type: deque(queue_state.waiters)
                    for input_type, queue_state in state_by_type.items()
                },
            )

    async def dequeue(
        self, input_type: Optional[str] = None
    ) -> Optional[state.Message]:
        async with self._lock:
            if input_type is not None:
                resolved_input_type = normalize_input_type(input_type)
                queue_state = self._state_by_type.get(resolved_input_type)
                if queue_state is None or not queue_state.queued_messages:
                    return None
                return queue_state.queued_messages.popleft()
            for resolved_input_type in ordered_input_types(self._state_by_type.keys()):
                queue_state = self._state_by_type[resolved_input_type]
                if queue_state.queued_messages:
                    return queue_state.queued_messages.popleft()
            return None

    async def remove_at(
        self,
        index: int,
        input_type: Optional[str] = None,
    ) -> Optional[state.Message]:
        async with self._lock:
            if input_type is not None:
                resolved_input_type = normalize_input_type(input_type)
                queue_state = self._state_by_type.get(resolved_input_type)
                if queue_state is None:
                    return None
                queue_size = len(queue_state.queued_messages)
                if index < 0:
                    index = queue_size + index
                if index < 0 or index >= queue_size:
                    return None
                message = queue_state.queued_messages[index]
                del queue_state.queued_messages[index]
                return message

            total_size = 0
            ordered_types = ordered_input_types(self._state_by_type.keys())
            for resolved_input_type in ordered_types:
                total_size += len(
                    self._state_by_type[resolved_input_type].queued_messages
                )
            if index < 0:
                index = total_size + index
            if index < 0 or index >= total_size:
                return None

            current_index = index
            for resolved_input_type in ordered_types:
                queue_state = self._state_by_type[resolved_input_type]
                queue_size = len(queue_state.queued_messages)
                if current_index < queue_size:
                    message = queue_state.queued_messages[current_index]
                    del queue_state.queued_messages[current_index]
                    return message
                current_index -= queue_size
            return None

    async def clear_queue(self, input_type: Optional[str] = None) -> int:
        async with self._lock:
            if input_type is not None:
                resolved_input_type = normalize_input_type(input_type)
                queue_state = self._state_by_type.get(resolved_input_type)
                if queue_state is None:
                    return 0
                count = len(queue_state.queued_messages)
                queue_state.queued_messages.clear()
                return count
            count = 0
            for queue_state in self._state_by_type.values():
                count += len(queue_state.queued_messages)
                queue_state.queued_messages.clear()
            return count

    async def reset(self) -> None:
        async with self._lock:
            for queue_state in self._state_by_type.values():
                for waiter in queue_state.waiters:
                    if waiter.done():
                        continue
                    waiter.cancel()
                queue_state.waiters.clear()

    async def reset_all(self) -> None:
        async with self._lock:
            for queue_state in self._state_by_type.values():
                queue_state.queued_messages.clear()
                for waiter in queue_state.waiters:
                    if waiter.done():
                        continue
                    waiter.cancel()
                queue_state.waiters.clear()
