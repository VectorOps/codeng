from __future__ import annotations

import asyncio

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.components import input_component as tui_input_component
from rich import box as rich_box
from vocode.tui.lib.input import posix as input_posix


class TextComponent(tui_terminal.Component):
    def __init__(
        self,
        text: str,
        id: str | None = None,
        show_id_prefix: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.text = text
        self.show_id_prefix = show_id_prefix

    def render(self) -> tui_terminal.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        text = self.text
        if self.show_id_prefix and self.id is not None:
            text = f"{self.id}: {text}"
        return terminal.console.render_lines(text)


async def _main() -> None:
    input_handler = input_posix.PosixInputHandler()
    terminal = tui_terminal.Terminal(input_handler=input_handler)
    header = TextComponent("Vocode TUI playground", id="header")
    help_text = TextComponent(
        "Type in the input box below.\n"
        "Commands:\n"
        "  /update <id> <text>",
        id="help",
    )
    input_component = tui_input_component.InputComponent(
        "",
        id="input",
        box_style=rich_box.SQUARE,
    )

    components: list[tui_terminal.Component] = []
    components.append(header)
    components.append(help_text)
    components.append(input_component)

    terminal.append_component(header)
    terminal.append_component(help_text)
    terminal.append_component(input_component)
    terminal.push_focus(input_component)

    counter = 1

    def append_message_component(message: str) -> None:
        nonlocal counter
        component_id = f"msg-{counter}"
        counter += 1
        component = TextComponent(
            message,
            id=component_id,
            show_id_prefix=True,
        )
        terminal.insert_component(-1, component)

    def handle_submit(value: str) -> None:
        stripped = value.strip()
        if not stripped:
            input_component.text = ""
            return

        if stripped.startswith("/"):
            if stripped.startswith("/update "):
                parts = stripped.split(" ", 2)
                if len(parts) < 3:
                    append_message_component("error: usage: /update <id> <text>")
                    input_component.text = ""
                    return
                _, target_id, new_text = parts
                try:
                    component = terminal.get_component(target_id)
                except KeyError:
                    append_message_component(f"error: component not found: {target_id}")
                    input_component.text = ""
                    return
                if not isinstance(component, TextComponent):
                    append_message_component(
                        f"error: unsupported component type for id {target_id}"
                    )
                    input_component.text = ""
                    return
                component.text = new_text
                terminal.notify_component(component)
                input_component.text = ""
                return

            append_message_component(f"error: unknown command: {value}")
            input_component.text = ""
            return

        append_message_component(value)
        input_component.text = ""

    input_component.subscribe_submit(handle_submit)

    await terminal.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await terminal.stop()


if __name__ == "__main__":
    asyncio.run(_main())
