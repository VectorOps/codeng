from __future__ import annotations

import asyncio
import typing
from rich import box as rich_box
from rich import console as rich_console

from vocode.tui import lib as tui_terminal
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.input import handler as input_handler_mod


class TUIState:
    def __init__(
        self,
        on_input: typing.Callable[[str], typing.Awaitable[None]],
        console: rich_console.Console | None = None,
    ) -> None:
        self._on_input = on_input
        input_handler = input_handler_mod.PosixInputHandler()
        settings = tui_terminal.TerminalSettings()
        self._terminal = tui_terminal.Terminal(
            console=console,
            input_handler=input_handler,
            settings=settings,
        )

        header = tui_markdown_component.MarkdownComponent("# Vocode TUI\n", id="header")
        input_style = tui_terminal.ComponentStyle(
            panel_box=rich_box.ROUNDED,
        )
        input_component = tui_input_component.InputComponent(
            "", id="input", single_line=True, component_style=input_style
        )

        self._input_component = input_component

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

    def add_markdown(self, markdown: str) -> None:
        component = tui_markdown_component.MarkdownComponent(markdown)
        self._terminal.insert_component(-1, component)

    def _handle_submit(self, value: str) -> None:
        stripped = value.strip()
        self._input_component.text = ""
        if not stripped:
            return
        asyncio.create_task(self._on_input(stripped))
