from __future__ import annotations

import typing

from rich import console as rich_console

from vocode.tui.lib import base as tui_base


class CompositeComponent(tui_base.Component):
    def __init__(
        self,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        self._children: list[tui_base.Component] = []
        self._focused_index: int | None = None
        self._terminal: tui_base.TerminalLike | None = None
        super().__init__(
            id=id,
            component_style=component_style,
        )

    @property
    def terminal(self) -> tui_base.TerminalLike | None:
        return self._terminal

    @terminal.setter
    def terminal(self, value: tui_base.TerminalLike | None) -> None:
        self._terminal = value
        for child in self._children:
            child.terminal = value

    @property
    def children(self) -> list[tui_base.Component]:
        return self._children

    def add_child(self, component: tui_base.Component) -> None:
        if component in self._children:
            return
        component.parent = self
        if self._terminal is not None:
            component.terminal = self._terminal
        self._children.append(component)
        self._mark_dirty()

    def remove_child(self, component: tui_base.Component) -> None:
        if component not in self._children:
            return
        index = self._children.index(component)
        del self._children[index]
        if self._focused_index is not None:
            if self._focused_index == index:
                self._focused_index = None
            elif self._focused_index > index:
                self._focused_index -= 1
        component.parent = None
        component.terminal = None
        self._mark_dirty()

    def focus_child(self, component: tui_base.Component) -> None:
        try:
            index = self._children.index(component)
        except ValueError:
            return
        self._focused_index = index

    def clear_focus(self) -> None:
        self._focused_index = None

    def _get_focused_child(self) -> tui_base.Component | None:
        index = self._focused_index
        if index is None:
            return None
        if index < 0 or index >= len(self._children):
            return None
        return self._children[index]

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        lines: tui_base.Lines = []
        for child in self._children:
            if child.is_hidden:
                continue
            child_lines = child.render(options)
            lines.extend(child_lines)
        return lines

    def on_key_event(self, event: typing.Any) -> None:
        child = self._get_focused_child()
        if child is not None:
            child.on_key_event(event)

    def on_mouse_event(self, event: typing.Any) -> None:
        child = self._get_focused_child()
        if child is not None:
            child.on_mouse_event(event)

    def set_collapsed(self, collapsed: bool) -> None:
        child = self._get_focused_child()
        if child is not None:
            child.set_collapsed(collapsed)
            return
        super().set_collapsed(collapsed)

    def toggle_collapsed(self) -> None:
        child = self._get_focused_child()
        if child is not None:
            child.toggle_collapsed()
            return
        super().toggle_collapsed()
