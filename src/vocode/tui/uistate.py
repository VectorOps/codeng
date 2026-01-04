from __future__ import annotations

import asyncio
import json
import typing
from rich import console as rich_console
from vocode import state as vocode_state
from vocode import models as vocode_models
from vocode.logger import logger
from vocode.tui import lib as tui_terminal
from vocode.tui import styles as tui_styles
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.components import rich_text_component as tui_rich_text_component
from vocode.tui.lib.input import base as input_base
from vocode.tui.lib.input import handler as input_handler_mod


class TUIState:
    def __init__(
        self,
        on_input: typing.Callable[[str], typing.Awaitable[None]],
        console: rich_console.Console | None = None,
        input_handler: input_base.InputHandler | None = None,
    ) -> None:
        self._on_input = on_input
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
            component_style=tui_styles.INPUT_COMPONENT_STYLE,
        )

        self._input_component = input_component
        self._step_components: dict[str, tui_markdown_component.MarkdownComponent] = {}
        self._step_handlers: dict[
            vocode_state.StepType, typing.Callable[[vocode_state.Step], None]
        ] = {
            vocode_state.StepType.OUTPUT_MESSAGE: self._handle_output_message_step,
            vocode_state.StepType.INPUT_MESSAGE: self._handle_input_message_step,
            vocode_state.StepType.PROMPT: self._handle_prompt_step,
            vocode_state.StepType.TOOL_REQUEST: self._handle_prompt_step,
        }

        self._terminal.append_component(header)
        self._terminal.append_component(input_component)
        self._terminal.push_focus(input_component)

        self._input_component.subscribe_submit(self._handle_submit)

    @property
    def terminal(self) -> tui_terminal.Terminal:
        return self._terminal

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
        self._terminal.insert_component(-1, component)

    def add_rich_text(
        self,
        text: str,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        component = tui_rich_text_component.RichTextComponent(
            text,
            component_style=component_style,
        )
        self._terminal.insert_component(-1, component)

    def _format_message_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        return message.text

    def _format_prompt_markdown(self, step: vocode_state.Step) -> str | None:
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
        self._terminal.insert_component(-1, component)

    def _handle_output_message_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_message_markdown(step)
        if markdown is None:
            return
        lines = markdown.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        trimmed = "\n".join(lines)
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

    def _handle_default_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_message_markdown(step)
        if markdown is None:
            return
        self.add_markdown(markdown)

    def handle_step(self, step: vocode_state.Step) -> None:
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

    def _handle_submit(self, value: str) -> None:
        stripped = value.strip()
        self._input_component.text = ""
        if not stripped:
            return
        asyncio.create_task(self._on_input(stripped))
