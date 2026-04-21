from __future__ import annotations

from vocode import models
from vocode import state as vocode_state

from . import output as command_output
from .base import command, option


MAX_LIST_ITEMS = 10


USAGE = (
    "Input queue commands:",
    [
        (
            "/queue list",
            "Show pending waiters and the first 10 queued input messages.",
        ),
        ("/queue add <text>", "Append a user message to the input queue."),
        (
            "/queue delete [number]",
            "Remove a queued input message by number, or the first message when omitted. Negative numbers count from the end.",
        ),
        ("/queue pop", "Remove the last queued input message."),
        ("/queue clear", "Remove all queued input messages."),
    ],
)


@command(
    "queue",
    description="Inspect and mutate the centralized input queue",
    params=["action", "args..."],
)
@option(0, "args", type=str, splat=True)
async def _queue(server, args: list[str]) -> None:
    if not args or args[0] == "help":
        await command_output.send_rich(server, command_output.format_help(*USAGE))
        return

    action = args[0]
    input_manager = server.manager.project.input_manager

    def _format_queue_text(text: str) -> str:
        lines = text.splitlines() or [text]
        visible_lines = [line.rstrip() for line in lines[:3]]
        formatted = "\n      ".join(visible_lines)
        if len(lines) > 3:
            formatted = f"{formatted}\n      ..."
        return formatted or "<empty>"

    if action in {"list", "peek", "snoop"}:
        if len(args) != 1:
            await command_output.send_warning(server, "Usage: /queue list")
            return
        snapshot = await input_manager.snapshot()
        lines = [
            command_output.heading("Input queue:"),
            f"  Pending waiters: {len(snapshot.waiters)}",
            f"  Queued messages: {len(snapshot.queued_messages)}",
        ]
        if snapshot.queued_messages:
            queued_messages = list(snapshot.queued_messages)
            for index, message in enumerate(queued_messages[:MAX_LIST_ITEMS], start=1):
                lines.append(f"\n[{index:>2}] {message.role.value}")
                lines.append(f"      {_format_queue_text(message.text)}")
            remaining = len(queued_messages) - MAX_LIST_ITEMS
            if remaining > 0:
                lines.append(f"\n... and {remaining} more")
        await command_output.send_rich(server, "\n".join(lines))
        return

    if action in {"add", "push"}:
        if len(args) < 2:
            await command_output.send_warning(server, "Usage: /queue add <text>")
            return
        text = " ".join(args[1:])
        message = vocode_state.Message(role=models.Role.USER, text=text)
        await input_manager.publish(message, queue=True)
        snapshot = await input_manager.snapshot()
        await command_output.send_success(
            server,
            f"Queued input message. Queue size: {len(snapshot.queued_messages)}",
        )
        return

    if action in {"delete", "del", "remove"}:
        if len(args) > 2:
            await command_output.send_warning(server, "Usage: /queue delete [number]")
            return
        if len(args) == 1:
            message = await input_manager.dequeue()
        else:
            try:
                queue_number = int(args[1])
            except ValueError:
                await command_output.send_warning(
                    server, "Usage: /queue delete [number]"
                )
                return
            if queue_number == 0:
                await command_output.send_warning(server, "Queue number must not be 0.")
                return
            queue_index = queue_number - 1 if queue_number > 0 else queue_number
            message = await input_manager.remove_at(queue_index)
        if message is None:
            await command_output.send_warning(server, "Input queue is empty.")
            return
        await command_output.send_success(
            server,
            f"Deleted input message: {message.role.value}: {message.text}",
        )
        return

    if action == "pop":
        if len(args) != 1:
            await command_output.send_warning(server, "Usage: /queue pop")
            return
        message = await input_manager.remove_at(-1)
        if message is None:
            await command_output.send_warning(server, "Input queue is empty.")
            return
        await command_output.send_success(
            server,
            f"Popped input message: {message.role.value}: {message.text}",
        )
        return

    if action == "clear":
        if len(args) != 1:
            await command_output.send_warning(server, "Usage: /queue clear")
            return
        count = await input_manager.clear_queue()
        await command_output.send_success(
            server,
            f"Cleared {count} queued input message(s).",
        )
        return

    await command_output.send_rich(server, command_output.format_help(*USAGE))
