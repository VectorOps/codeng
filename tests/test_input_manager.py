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
async def test_input_manager_only_new_wait_ignores_queued_messages() -> None:
    manager = InputManager()
    queued_message = state.Message(role=models.Role.USER, text="queued")
    fresh_message = state.Message(role=models.Role.USER, text="fresh")

    accepted = await manager.publish(
        queued_message,
        queue=True,
    )
    assert accepted is True

    task = asyncio.create_task(manager.wait_for_input(only_new=True))
    await asyncio.sleep(0)

    accepted = await manager.publish(
        fresh_message,
        queue=False,
    )
    received = await task

    assert accepted is True
    assert received == fresh_message

    snapshot = await manager.snapshot()
    assert [message.text for message in snapshot.queued_messages] == ["queued"]


@pytest.mark.asyncio
async def test_input_manager_snapshot_and_dequeue_reflect_queued_messages() -> None:
    manager = InputManager()
    first = state.Message(role=models.Role.USER, text="one")
    second = state.Message(role=models.Role.USER, text="two")

    assert await manager.publish(first, queue=True) is True
    assert await manager.publish(second, queue=True) is True

    snapshot = await manager.snapshot()

    assert [message.text for message in snapshot.queued_messages] == ["one", "two"]
    assert len(snapshot.waiters) == 0

    dequeued = await manager.dequeue()

    assert dequeued is not None
    assert dequeued.text == "one"

    snapshot = await manager.snapshot()
    assert [message.text for message in snapshot.queued_messages] == ["two"]


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
