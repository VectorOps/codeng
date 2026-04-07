from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from vocode import state


@dataclass
class InputChannelState:
    queued_messages: Deque[state.Message]
    waiters: Deque[asyncio.Future[state.Message]]


class InputManager:
    def __init__(self) -> None:
        self._channels: Dict[str, InputChannelState] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_channel(self, workflow_id: str) -> InputChannelState:
        channel = self._channels.get(workflow_id)
        if channel is None:
            channel = InputChannelState(
                queued_messages=deque(),
                waiters=deque(),
            )
            self._channels[workflow_id] = channel
        return channel

    def _prune_channel_if_empty(self, workflow_id: str) -> None:
        channel = self._channels.get(workflow_id)
        if channel is None:
            return
        if channel.queued_messages:
            return
        for waiter in channel.waiters:
            if not waiter.done():
                return
        self._channels.pop(workflow_id, None)

    async def publish(
        self,
        workflow_id: str,
        message: state.Message,
        *,
        queue_if_unhandled: bool,
    ) -> bool:
        async with self._lock:
            channel = self._get_or_create_channel(workflow_id)
            while channel.waiters:
                waiter = channel.waiters.popleft()
                if waiter.done():
                    continue
                waiter.set_result(message)
                self._prune_channel_if_empty(workflow_id)
                return True
            if not queue_if_unhandled:
                self._prune_channel_if_empty(workflow_id)
                return False
            channel.queued_messages.append(message)
            return True

    async def wait_for_input(self, workflow_id: str) -> state.Message:
        waiter: Optional[asyncio.Future[state.Message]] = None
        async with self._lock:
            channel = self._get_or_create_channel(workflow_id)
            if channel.queued_messages:
                message = channel.queued_messages.popleft()
                self._prune_channel_if_empty(workflow_id)
                return message
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            channel.waiters.append(waiter)

        try:
            return await waiter
        finally:
            async with self._lock:
                channel = self._channels.get(workflow_id)
                if channel is not None:
                    try:
                        channel.waiters.remove(waiter)
                    except ValueError:
                        pass
                    self._prune_channel_if_empty(workflow_id)

    async def reset_workflow(self, workflow_id: str) -> None:
        async with self._lock:
            channel = self._channels.pop(workflow_id, None)
            if channel is None:
                return
            channel.queued_messages.clear()
            for waiter in channel.waiters:
                if waiter.done():
                    continue
                waiter.cancel()
            channel.waiters.clear()

    async def reset_all(self) -> None:
        async with self._lock:
            channels = self._channels
            self._channels = {}
            for channel in channels.values():
                channel.queued_messages.clear()
                for waiter in channel.waiters:
                    if waiter.done():
                        continue
                    waiter.cancel()
                channel.waiters.clear()
