from __future__ import annotations

from vocode import models
from vocode import state as vocode_state

from .base import command, option


MAX_LIST_ITEMS = 10


USAGE = (
    "Input queue commands:\n\n"
    "  /queue list\n"
    "    Show pending waiters and the first 10 queued input messages.\n\n"
    "  /queue add <text>\n"
    "    Append a user message to the input queue.\n\n"
    "  /queue delete [number]\n"
    "    Remove a queued input message by number, or the top message when omitted.\n\n"
    "  /queue clear\n"
    "    Remove all queued input messages.\n"
)


@command(
    "queue",
    description="Inspect and mutate the centralized input queue",
    params=["action", "args..."],
)
@option(0, "args", type=str, splat=True)
async def _queue(server, args: list[str]) -> None:
    if not args or args[0] == "help":
        await server.send_text_message(USAGE)
        return

    action = args[0]
    input_manager = server.manager.project.input_manager

    if action in {"list", "peek", "snoop"}:
        if len(args) != 1:
            await server.send_text_message("Usage: /queue list")
            return
        snapshot = await input_manager.snapshot()
        lines = [
            f"Pending waiters: {len(snapshot.waiters)}",
            f"Queued messages: {len(snapshot.queued_messages)}",
        ]
        if snapshot.queued_messages:
            queued_messages = list(snapshot.queued_messages)
            for index, message in enumerate(queued_messages[:MAX_LIST_ITEMS], start=1):
                lines.append(f"{index}. {message.role.value}: {message.text}")
            remaining = len(queued_messages) - MAX_LIST_ITEMS
            if remaining > 0:
                lines.append(f"... and {remaining} more")
        await server.send_text_message("\n".join(lines))
        return

    if action in {"add", "push"}:
        if len(args) < 2:
            await server.send_text_message("Usage: /queue add <text>")
            return
        text = " ".join(args[1:])
        message = vocode_state.Message(role=models.Role.USER, text=text)
        await input_manager.publish(message, queue=True)
        snapshot = await input_manager.snapshot()
        await server.send_text_message(
            f"Queued input message. Queue size: {len(snapshot.queued_messages)}"
        )
        return

    if action in {"delete", "del", "remove", "pop"}:
        if len(args) > 2:
            await server.send_text_message("Usage: /queue delete [number]")
            return
        if len(args) == 1:
            message = await input_manager.dequeue()
        else:
            try:
                queue_number = int(args[1])
            except ValueError:
                await server.send_text_message("Usage: /queue delete [number]")
                return
            if queue_number <= 0:
                await server.send_text_message("Queue number must be greater than 0.")
                return
            message = await input_manager.remove_at(queue_number - 1)
        if message is None:
            await server.send_text_message("Input queue is empty.")
            return
        await server.send_text_message(
            f"Deleted input message: {message.role.value}: {message.text}"
        )
        return

    if action == "clear":
        if len(args) != 1:
            await server.send_text_message("Usage: /queue clear")
            return
        count = await input_manager.clear_queue()
        await server.send_text_message(f"Cleared {count} queued input message(s).")
        return

    await server.send_text_message(USAGE)
