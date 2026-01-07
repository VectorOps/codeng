from __future__ import annotations

import typing

from rich import console as rich_console

from vocode.tui.lib import base as tui_base


class CallbackRenderableComponent(tui_base.Component):
    def __init__(
        self,
        render_fn: typing.Callable[[rich_console.Console], tui_base.Renderable],
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._render_fn = render_fn

    @property
    def render_fn(
        self,
    ) -> typing.Callable[[rich_console.Console], tui_base.Renderable]:
        return self._render_fn

    @render_fn.setter
    def render_fn(
        self,
        value: typing.Callable[[rich_console.Console], tui_base.Renderable],
    ) -> None:
        if value is self._render_fn:
            return
        self._render_fn = value
        self._mark_dirty()

    def set_render_fn(
        self,
        render_fn: typing.Callable[[rich_console.Console], tui_base.Renderable],
    ) -> None:
        self.render_fn = render_fn

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        renderable = self._render_fn(console)
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        return typing.cast(tui_base.Lines, rendered)

    def _mark_dirty(self) -> None:
        terminal = self.terminal
        if terminal is not None:
            terminal.notify_component(self)
