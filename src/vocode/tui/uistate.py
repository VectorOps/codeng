from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import typing
from rich import console as rich_console
from vocode import state as vocode_state
from vocode import models as vocode_models
from vocode.logger import logger
from vocode.manager import proto as manager_proto
from vocode.tui import lib as tui_terminal
from vocode.tui import styles as tui_styles
from vocode.tui import history as tui_history
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.components import rich_text_component as tui_rich_text_component
from vocode.tui.lib.components import select_list as tui_select_list
from vocode.tui.lib.input import base as input_base
from vocode.tui.lib.input import handler as input_handler_mod


AUTOCOMPLETE_DEBOUNCE_MS: typing.Final[int] = 250


class ActionKind(str, enum.Enum):
    DEFAULT = "default"
    AUTOCOMPLETE = "autocomplete"


@dataclasses.dataclass
class ActionItem:
    kind: ActionKind
    component: tui_terminal.Component


class TUIState:
    def __init__(
        self,
        on_input: typing.Callable[[str], typing.Awaitable[None]],
        console: rich_console.Console | None = None,
        input_handler: input_base.InputHandler | None = None,
        on_autocomplete_request: (
            typing.Callable[[str, int, int], typing.Awaitable[None]] | None
        ) = None,
        on_stop: typing.Callable[[], typing.Awaitable[None]] | None = None,
        on_eof: typing.Callable[[], typing.Awaitable[None]] | None = None,
    ) -> None:
        self._on_input = on_input
        self._on_autocomplete_request = on_autocomplete_request
        self._on_stop = on_stop
        self._on_eof = on_eof
        if input_handler is None:
            input_handler = input_handler_mod.PosixInputHandler()
        settings = tui_terminal.TerminalSettings()
        self._terminal = tui_terminal.Terminal(
            console=console,
            input_handler=input_handler,
            settings=settings,
        )

        header = tui_markdown_component.MarkdownComponent("# Vocode TUI\n", id="header")
        input_component = tui_input_component.InputComponent(
            "",
            id="input",
            prefix="> ",
            component_style=tui_styles.INPUT_PANEL_COMPONENT_STYLE,
        )

        self._input_component = input_component
        self._history_manager = tui_history.HistoryManager()
        self._input_keymap = self._create_input_keymap()
        self._input_component.set_key_event_handler(self._handle_input_key_event)
        self._step_components: dict[str, tui_markdown_component.MarkdownComponent] = {}
        self._step_handlers: dict[
            vocode_state.StepType, typing.Callable[[vocode_state.Step], None]
        ] = {
            vocode_state.StepType.OUTPUT_MESSAGE: self._handle_output_message_step,
            vocode_state.StepType.INPUT_MESSAGE: self._handle_input_message_step,
            vocode_state.StepType.APPROVAL: self._handle_approval_step,
            vocode_state.StepType.REJECTION: self._handle_rejection_step,
            vocode_state.StepType.PROMPT: self._handle_prompt_step,
            vocode_state.StepType.PROMPT_CONFIRM: self._handle_prompt_step,
            vocode_state.StepType.TOOL_REQUEST: self._handle_tool_request_step,
        }

        self._terminal.append_component(header)
        self._terminal.append_component(input_component)

        toolbar = tui_rich_text_component.RichTextComponent(
            "", id="toolbar", component_style=tui_styles.TOOLBAR_COMPONENT_STYLE
        )
        self._base_toolbar_component = toolbar
        self._toolbar_component = toolbar
        self._terminal.append_component(toolbar)

        self._action_stack: list[ActionItem] = [
            ActionItem(kind=ActionKind.DEFAULT, component=toolbar)
        ]

        self._terminal.push_focus(input_component)

        self._input_component.subscribe_submit(self._handle_submit)
        self._input_component.subscribe_cursor_event(self._handle_cursor_event)

        self._autocomplete_task: asyncio.Task[None] | None = None
        self._last_autocomplete_text: str | None = None
        self._last_autocomplete_row: int | None = None
        self._last_autocomplete_col: int | None = None
        self._ui_state: manager_proto.UIServerStatePacket | None = None

    @property
    def terminal(self) -> tui_terminal.Terminal:
        return self._terminal

    @property
    def history(self) -> tui_history.HistoryManager:
        return self._history_manager

    @property
    def last_ui_state(self) -> manager_proto.UIServerStatePacket | None:
        return self._ui_state

    def _create_input_keymap(
        self,
    ) -> dict[
        tui_input_component.KeyBinding, typing.Callable[[input_base.KeyEvent], bool]
    ]:
        return {
            tui_input_component.KeyBinding("up"): self._handle_history_up,
            tui_input_component.KeyBinding("p", ctrl=True): self._handle_history_up,
            tui_input_component.KeyBinding("down"): self._handle_history_down,
            tui_input_component.KeyBinding("n", ctrl=True): self._handle_history_down,
            tui_input_component.KeyBinding("c", ctrl=True): self._handle_stop,
            tui_input_component.KeyBinding("d", ctrl=True): self._handle_eof,
        }

    def _handle_input_key_event(self, event: input_base.KeyEvent) -> bool:
        top = self._action_stack[-1]
        if top.kind is ActionKind.AUTOCOMPLETE:
            component = typing.cast(tui_select_list.SelectListComponent, top.component)
            if event.key in ("up", "down", "enter", "tab", "esc", "escape"):
                if event.action == "down":
                    mapped_key = event.key
                    if mapped_key == "tab":
                        mapped_key = "enter"
                    mapped_event = input_base.KeyEvent(
                        action="down",
                        key=mapped_key,
                        ctrl=False,
                        alt=False,
                        shift=False,
                    )
                    component.on_key_event(mapped_event)
                return True
        binding = tui_input_component.KeyBinding(
            key=event.key,
            ctrl=event.ctrl,
            alt=event.alt,
            shift=event.shift,
        )
        handler = self._input_keymap.get(binding)
        if handler is None:
            return False
        return handler(event)

    def _handle_history_up(self, event: input_base.KeyEvent) -> bool:
        component = self._input_component
        if component.cursor_row != 0:
            return False
        new_text = self._history_manager.navigate_previous(component.text)
        if new_text is None:
            return False
        component.text = new_text
        lines = component.lines
        if lines:
            last_row = len(lines) - 1
            last_col = len(lines[last_row])
            component.set_cursor_position(last_row, last_col)
        return True

    def _handle_history_down(self, event: input_base.KeyEvent) -> bool:
        component = self._input_component
        lines = component.lines
        if not lines:
            return False
        last_row = len(lines) - 1
        if component.cursor_row != last_row:
            return False
        new_text = self._history_manager.navigate_next()
        if new_text is None:
            return False
        component.text = new_text
        lines = component.lines
        if lines:
            component.set_cursor_position(0, 0)
        return True

    def _handle_stop(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if self._on_stop is None:
            return False
        asyncio.create_task(self._on_stop())
        return True

    def _handle_eof(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if self._on_eof is None:
            return False
        asyncio.create_task(self._on_eof())
        return True

    async def start(self) -> None:
        await self._terminal.start()
        await self._terminal.render()

    async def stop(self) -> None:
        await self._terminal.stop()

    def add_markdown(
        self,
        markdown: str,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        component = tui_markdown_component.MarkdownComponent(
            markdown,
            component_style=component_style,
        )
        self._terminal.insert_component(-2, component)

    def add_rich_text(
        self,
        text: str,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        component = tui_rich_text_component.RichTextComponent(
            text,
            component_style=component_style,
        )
        self._terminal.insert_component(-2, component)

    def add_text_message(
        self,
        text: str,
        text_format: str = "plain",
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        if component_style is None:
            component_style = tui_styles.OUTPUT_MESSAGE_STYLE
        if text_format == "markdown":
            self.add_markdown(text, component_style=component_style)
        else:
            self.add_rich_text(text, component_style=component_style)

    def _format_message_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        return message.text

    def _format_prompt_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        if message.text is None:
            return None
        return message.text

    def _format_tool_request_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        parts: list[str] = []
        if message.text is not None:
            parts.append(message.text)
        tool_calls = message.tool_call_requests
        if tool_calls:
            for tool_call in tool_calls:
                parts.append("")
                parts.append(f"**Tool call:** `{tool_call.name}`")
                arguments = tool_call.arguments
                if arguments:
                    try:
                        args_str = json.dumps(arguments, indent=2, sort_keys=True)
                    except TypeError:
                        args_str = str(arguments)
                    parts.append("```json")
                    parts.append(args_str)
                    parts.append("```")
        if not parts:
            return None
        return "\n".join(parts)

    def _upsert_markdown_component(
        self,
        step: vocode_state.Step,
        markdown: str,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        step_id = str(step.id)
        existing = self._step_components.get(step_id)
        if existing is not None:
            existing.markdown = markdown
            if component_style is not None:
                existing.component_style = component_style
            return
        component = tui_markdown_component.MarkdownComponent(
            markdown,
            id=step_id,
            component_style=component_style,
        )
        self._step_components[step_id] = component
        self._terminal.insert_component(-2, component)

    def _handle_output_message_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_message_markdown(step)
        if markdown is None:
            return
        trimmed = markdown.strip()
        self._upsert_markdown_component(
            step,
            trimmed,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_input_message_step(self, step: vocode_state.Step) -> None:
        raw = self._format_message_markdown(step)
        if raw is None:
            return

        prefix = self._input_component.prefix or ""
        lines = raw.splitlines() if raw else [""]

        if prefix and lines:
            pad = " " * len(prefix)
            first_line = prefix + lines[0]
            if len(lines) > 1:
                rest_lines = [pad + line for line in lines[1:]]
                all_lines = [first_line, *rest_lines]
            else:
                all_lines = [first_line]
            prefixed = "\n".join(all_lines)
        else:
            prefixed = "\n".join(lines)

        self.add_rich_text(
            prefixed,
            component_style=tui_styles.INPUT_MESSAGE_COMPONENT_STYLE,
        )

    def _handle_prompt_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_prompt_markdown(step)
        if markdown is None:
            return
        self._upsert_markdown_component(
            step,
            markdown,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_approval_step(self, step: vocode_state.Step) -> None:
        _ = step
        self.add_rich_text(
            "User approved.",
            component_style=tui_styles.INPUT_MESSAGE_COMPONENT_STYLE,
        )

    def _handle_rejection_step(self, step: vocode_state.Step) -> None:
        message = step.message
        text = "User declined."
        if message is not None:
            raw = message.text.strip()
            if raw:
                text = raw
        self.add_markdown(
            text,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_tool_request_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_tool_request_markdown(step)
        if markdown is None:
            return
        self._upsert_markdown_component(
            step,
            markdown,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_default_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_message_markdown(step)
        if markdown is None:
            return
        self.add_markdown(markdown)

    def handle_step(self, step: vocode_state.Step) -> None:
        logger.info("uistep", step=step)
        mode = step.output_mode
        if mode == vocode_models.OutputMode.HIDE_ALL:
            if step.message is not None:
                return
        elif mode == vocode_models.OutputMode.HIDE_FINAL:
            if step.is_final and step.message is not None:
                return
        handler = self._step_handlers.get(step.type)
        if handler is not None:
            handler(step)
            return
        self._handle_default_step(step)

    def handle_ui_state(self, packet: manager_proto.UIServerStatePacket) -> None:
        self._ui_state = packet
        self._update_toolbar_from_ui_state()

    def set_input_panel_title(
        self,
        title: str | None,
        subtitle: str | None = None,
    ) -> None:
        style = self._input_component.component_style
        if style is None:
            style = tui_styles.INPUT_PANEL_COMPONENT_STYLE
        if style is None:
            return
        new_style = dataclasses.replace(
            style,
            panel_title=title,
            panel_subtitle=subtitle,
        )
        self._input_component.component_style = new_style
        self._terminal.notify_component(self._input_component)

    def _handle_submit(self, value: str) -> None:
        stripped = value.strip()
        self._history_manager.add(stripped)
        self._input_component.text = ""
        # TODO: Configurable
        # if not stripped:
        #    return
        asyncio.create_task(self._on_input(stripped))

    def _handle_cursor_event(self, row: int, col: int) -> None:
        if self._on_autocomplete_request is None:
            return
        lines = self._input_component.lines
        if row < 0 or row >= len(lines):
            return
        line = lines[row]
        if col < 0:
            col = 0
        if col > len(line):
            col = len(line)
        end = col
        while end < len(line) and not line[end].isspace():
            end += 1
        text = line[:end]
        self._last_autocomplete_text = text
        self._last_autocomplete_row = row
        self._last_autocomplete_col = col
        if self._autocomplete_task is not None and not self._autocomplete_task.done():
            self._autocomplete_task.cancel()
        loop = asyncio.get_running_loop()

        async def _debounced() -> None:
            try:
                await asyncio.sleep(AUTOCOMPLETE_DEBOUNCE_MS / 1000.0)
            except asyncio.CancelledError:
                return
            current_text = self._last_autocomplete_text
            current_row = self._last_autocomplete_row
            current_col = self._last_autocomplete_col
            if (
                current_text is None
                or current_row is None
                or current_col is None
            ):
                return
            await self._on_autocomplete_request(
                current_text,
                current_row,
                current_col,
            )

        self._autocomplete_task = loop.create_task(_debounced())

    def handle_autocomplete_options(
        self,
        items: list[manager_proto.AutocompleteItem] | None,
    ) -> None:
        logger.info("auto", items=items)
        if not items:
            self._pop_autocomplete()
            return
        top = self._action_stack[-1]
        if top.kind is ActionKind.AUTOCOMPLETE:
            component = typing.cast(tui_select_list.SelectListComponent, top.component)
            component.set_items(
                [
                    {
                        "id": str(index),
                        "text": item.title,
                        "value": item.value,
                    }
                    for index, item in enumerate(items)
                ]
            )
            return
        select = tui_select_list.SelectListComponent(id="autocomplete")

        def _on_select(item: tui_select_list.SelectItem | None) -> None:
            if item is None:
                self._pop_autocomplete()
                return
            value = item.value if item.value is not None else item.text
            lines = self._input_component.lines
            row = self._input_component.cursor_row
            col = self._input_component.cursor_col
            if row < 0 or row >= len(lines):
                self._pop_autocomplete()
                return
            line = lines[row]
            if col < 0:
                col = 0
            if col > len(line):
                col = len(line)
            start = col
            while start > 0 and not line[start - 1].isspace():
                start -= 1
            end = col
            while end < len(line) and not line[end].isspace():
                end += 1
            insert_value = value
            if line.startswith("/run"):
                prefix = "/run"
                if (
                    start == 0
                    and end <= len(prefix)
                    and (len(line) <= len(prefix) or line[len(prefix)] != " ")
                ):
                    insert_value = f"{prefix} {value}"
            # If completing a command at the start of the line, append a space
            if line.startswith("/") and start == 0 and value.startswith("/"):
                if not value.endswith(" "):
                    insert_value = value + " "

            new_line = line[:start] + insert_value + line[end:]
            all_lines = list(lines)
            all_lines[row] = new_line
            self._input_component.text = "\n".join(all_lines)
            self._input_component.set_cursor_position(row, start + len(insert_value))
            self._pop_autocomplete()

        select.subscribe_select(_on_select)
        select.set_items(
            [
                {
                    "id": str(index),
                    "text": item.title,
                    "value": item.value,
                }
                for index, item in enumerate(items)
            ]
        )
        self._push_action(ActionKind.AUTOCOMPLETE, select)

    def _push_action(self, kind: ActionKind, component: tui_terminal.Component) -> None:
        current_component = self._action_stack[-1].component
        if component is not current_component:
            terminal = self._terminal
            if terminal is not None:
                terminal.remove_component(current_component)
                terminal.append_component(component)
        self._action_stack.append(ActionItem(kind=kind, component=component))
        self._toolbar_component = component

    def _pop_autocomplete(self) -> None:
        if len(self._action_stack) <= 1:
            return
        top = self._action_stack[-1]
        if top.kind is not ActionKind.AUTOCOMPLETE:
            return
        terminal = self._terminal
        if terminal is not None:
            terminal.remove_component(top.component)
        self._action_stack.pop()
        self._toolbar_component = self._action_stack[-1].component
        if terminal is not None:
            if self._toolbar_component not in terminal.components:
                terminal.append_component(self._toolbar_component)

    def _update_toolbar_from_ui_state(self) -> None:
        toolbar = self._base_toolbar_component
        ui_state = self._ui_state
        text = ""
        if ui_state is not None and ui_state.runners:
            frame = ui_state.runners[-1]
            workflow_name = frame.workflow_name
            node_name = frame.node_name
            if node_name:
                text = f"{workflow_name}@{node_name}"
            else:
                text = workflow_name
        toolbar.text = text
