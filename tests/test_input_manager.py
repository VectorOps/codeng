from __future__ import annotations

import asyncio

import pytest

from vocode import models, state
from vocode.input_manager import InputManager


@pytest.mark.asyncio
async def test_input_manager_rejects_unhandled_non_queued_publish() -> None:
    manager = InputManager()

    accepted = await manager.publish(
        state.Message(role=models.Role.USER, text="hello"),
        queue=False,
    )

    assert accepted is False


@pytest.mark.asyncio
async def test_input_manager_queues_message_when_requested() -> None:
    manager = InputManager()
    message = state.Message(role=models.Role.USER, text="hello")

    accepted = await manager.publish(
        message,
        queue=True,
    )
    received = await manager.wait_for_input()

    assert accepted is True
    assert received == message


@pytest.mark.asyncio
async def test_input_manager_delivers_directly_to_waiter() -> None:
    manager = InputManager()
    task = asyncio.create_task(manager.wait_for_input())
    await asyncio.sleep(0)
    message = state.Message(role=models.Role.USER, text="hello")

    accepted = await manager.publish(
        message,
        queue=False,
    )
    received = await task

    assert accepted is True
    assert received == message


@pytest.mark.asyncio
async def test_input_manager_cleans_up_canceled_waiter() -> None:
    manager = InputManager()
    task = asyncio.create_task(manager.wait_for_input())
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    accepted = await manager.publish(
        state.Message(role=models.Role.USER, text="hello"),
        queue=False,
    )

    assert accepted is False


@pytest.mark.asyncio
async def test_input_manager_reset_clears_queue_and_cancels_waiter() -> None:
    manager = InputManager()
    waiter = asyncio.create_task(manager.wait_for_input())
    await asyncio.sleep(0)

    await manager.reset()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    accepted = await manager.publish(
        state.Message(role=models.Role.USER, text="queued"),
        queue=True,
    )
    assert accepted is True

    await manager.reset()

    accepted_after_reset = await manager.publish(
        state.Message(role=models.Role.USER, text="hello"),
        queue=False,
    )

    assert accepted_after_reset is False
