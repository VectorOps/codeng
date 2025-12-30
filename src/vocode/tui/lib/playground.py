from __future__ import annotations

import asyncio

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.components import select_list as tui_select_list
from vocode.tui.lib.input import posix as input_posix
from rich import console as rich_console
from rich import box as rich_box


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

    def render(
        self,
        options: rich_console.ConsoleOptions,
    ) -> tui_terminal.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        text = self.text
        if self.show_id_prefix and self.id is not None:
            text = f"{self.id}: {text}"
        return terminal.console.render_lines(text, options=options)


async def _main() -> None:
    input_handler = input_posix.PosixInputHandler()
    terminal = tui_terminal.Terminal(input_handler=input_handler)
    header = TextComponent("Vocode TUI playground", id="header")
    help_text = TextComponent(
        "Type in the input box below.\n"
        "Commands:\n"
        "  /update <id> <text>\n"
        "  /select\n"
        "    <option 1>\n"
        "    <option 2>\n"
        "    ...",
        id="help",
    )
    input_style = tui_terminal.ComponentStyle(
        panel_box=rich_box.ROUNDED,
    )
    input_component = tui_input_component.InputComponent(
        "",
        id="input",
        component_style=input_style,
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
    select_counter = 1

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
        nonlocal select_counter
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

            if stripped.startswith("/select"):
                lines = value.split("\n")
                if not lines:
                    append_message_component("error: usage: /select\n<option>...")
                    input_component.text = ""
                    return
                option_lines = [line for line in lines[1:] if line.strip()]
                if not option_lines:
                    append_message_component("error: /select requires option lines")
                    input_component.text = ""
                    return
                select_id = f"select-{select_counter}"
                select_counter += 1
                items = [
                    tui_select_list.SelectItem(
                        id=f"{select_id}-item-{index}",
                        text=line,
                    )
                    for index, line in enumerate(option_lines, start=1)
                ]
                select_component = tui_select_list.SelectListComponent(
                    items=items,
                    id=select_id,
                    component_style=tui_terminal.ComponentStyle(
                        padding_pad=1,
                        padding_style="on rgb(65,65,65)",
                    ),
                )

                def handle_select(item: tui_select_list.SelectItem) -> None:
                    terminal.remove_component(select_component)
                    terminal.remove_focus(select_component)
                    append_message_component(item.text)
                    input_component.text = ""

                select_component.subscribe_select(handle_select)
                terminal.insert_component(-1, select_component)
                terminal.push_focus(select_component)
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
