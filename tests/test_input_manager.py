from __future__ import annotations

import asyncio

import pytest

from vocode import models, state
from vocode.input_manager import InputManager


@pytest.mark.asyncio
async def test_input_manager_rejects_unhandled_non_queued_publish() -> None:
    manager = InputManager()

    accepted = await manager.publish(
        "workflow-1",
        state.Message(role=models.Role.USER, text="hello"),
        queue_if_unhandled=False,
    )

    assert accepted is False


@pytest.mark.asyncio
async def test_input_manager_queues_message_when_requested() -> None:
    manager = InputManager()
    message = state.Message(role=models.Role.USER, text="hello")

    accepted = await manager.publish(
        "workflow-1",
        message,
        queue_if_unhandled=True,
    )
    received = await manager.wait_for_input("workflow-1")

    assert accepted is True
    assert received == message


@pytest.mark.asyncio
async def test_input_manager_delivers_directly_to_waiter() -> None:
    manager = InputManager()
    task = asyncio.create_task(manager.wait_for_input("workflow-1"))
    await asyncio.sleep(0)
    message = state.Message(role=models.Role.USER, text="hello")

    accepted = await manager.publish(
        "workflow-1",
        message,
        queue_if_unhandled=False,
    )
    received = await task

    assert accepted is True
    assert received == message


@pytest.mark.asyncio
async def test_input_manager_cleans_up_canceled_waiter() -> None:
    manager = InputManager()
    task = asyncio.create_task(manager.wait_for_input("workflow-1"))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    accepted = await manager.publish(
        "workflow-1",
        state.Message(role=models.Role.USER, text="hello"),
        queue_if_unhandled=False,
    )

    assert accepted is False


@pytest.mark.asyncio
async def test_input_manager_reset_workflow_clears_queue_and_cancels_waiter() -> None:
    manager = InputManager()
    queued_message = state.Message(role=models.Role.USER, text="queued")
    accepted = await manager.publish(
        "workflow-1",
        queued_message,
        queue_if_unhandled=True,
    )
    waiter = asyncio.create_task(manager.wait_for_input("workflow-2"))
    await asyncio.sleep(0)

    await manager.reset_workflow("workflow-1")
    await manager.reset_workflow("workflow-2")

    assert accepted is True
    with pytest.raises(asyncio.CancelledError):
        await waiter

    accepted_after_reset = await manager.publish(
        "workflow-1",
        state.Message(role=models.Role.USER, text="hello"),
        queue_if_unhandled=False,
    )

    assert accepted_after_reset is False
