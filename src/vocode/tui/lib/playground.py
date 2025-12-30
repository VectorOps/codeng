from __future__ import annotations

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib import controls as tui_controls


class TextComponent(tui_terminal.Component):
    def __init__(self, text: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.text = text
    def render(self) -> tui_terminal.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        return terminal.console.render_lines(self.text)


def main() -> None:
    terminal = tui_terminal.Terminal()
    header = TextComponent("Vocode TUI playground", id="header")
    help_text = TextComponent(
        "Type text and press enter. Type 'q' to quit.\n"
        "Commands:\n"
        "  /list\n"
        "  /update <id> <text>",
        id="help",
    )

    components: list[tui_terminal.Component] = []
    components.append(header)
    components.append(help_text)

    terminal.append_component(header)
    terminal.append_component(help_text)
    terminal.render()

    counter = 1

    def append_component(component: tui_terminal.Component) -> None:
        components.append(component)
        terminal.append_component(component)

    def print_message(message: str) -> None:
        nonlocal counter
        component_id = f"msg-{counter}"
        counter += 1
        component = TextComponent(message, id=component_id)
        append_component(component)
        terminal.render()

    while True:
        user_input = input("> ")
        terminal.console.control(
            tui_controls.CustomControl.cursor_previous_line(1),
            tui_controls.CustomControl.erase_down(),
        )
        if user_input.strip().lower() == "q":
            break

        if user_input.startswith("/"):
            stripped = user_input.strip()

            if stripped == "/list":
                lines_text: list[str] = []
                lines_text.append(f"cursor_line:{terminal._cursor_line}")
                for component in terminal._components:
                    component_id = component.id
                    if component_id is None:
                        continue
                    rendered = component.render()
                    line_count = len(rendered)
                    lines_text.append(f"id:{component_id} lines:{line_count}")
                if lines_text:
                    message = "\n".join(lines_text)
                else:
                    message = "(no components)"
                print_message(message)
                continue

            if user_input.startswith("/update "):
                parts = user_input.split(" ", 2)
                if len(parts) < 3:
                    print_message("error: usage: /update <id> <text>")
                    continue
                _, target_id, new_text = parts
                try:
                    component = terminal.get_component(target_id)
                except KeyError:
                    print_message(f"error: component not found: {target_id}")
                    continue
                if not isinstance(component, TextComponent):
                    print_message(
                        f"error: unsupported component type for id {target_id}"
                    )
                    continue
                component.text = new_text
                terminal.notify_component(component)
                terminal.render()
                continue

            print_message(f"error: unknown command: {user_input}")
            continue

        component_id = f"msg-{counter}"
        counter += 1
        component = TextComponent(user_input, id=component_id)
        append_component(component)
        terminal.render()


if __name__ == "__main__":
    main()
