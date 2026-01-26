from __future__ import annotations

import typing

from pydantic import BaseModel
from rich import console as rich_console
from rich import segment as rich_segment
from rich import style as rich_style
from rich import text as rich_text

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.input import base as input_base


MAX_VISIBLE_ITEMS: typing.Final[int] = 5
SELECTED_STYLE: typing.Final[rich_style.Style] = rich_style.Style(reverse=True)


class SelectItem(BaseModel):
    id: str
    text: str
    value: str | None = None


class SelectListComponent(tui_base.Component):
    def __init__(
        self,
        items: (
            typing.Iterable[SelectItem | typing.Mapping[str, typing.Any]] | None
        ) = None,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
        allow_no_selection: bool = False,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._items: list[SelectItem] = []
        self._selected_index: int | None = None
        self._view_offset: int = 0
        self._select_subscribers: list[typing.Callable[[SelectItem | None], None]] = []
        self._allow_no_selection = allow_no_selection
        if items is not None:
            self.set_items(items)

    @property
    def items(self) -> list[SelectItem]:
        return list(self._items)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    @property
    def selected_item(self) -> SelectItem | None:
        if not self._items:
            return None
        index = self._selected_index
        if index is None:
            return None
        if index < 0 or index >= len(self._items):
            return None
        return self._items[index]

    def set_items(
        self,
        items: typing.Iterable[SelectItem | typing.Mapping[str, typing.Any]],
    ) -> None:
        prev_selected_index: typing.Optional[int] = self._selected_index
        prev_selected_id: typing.Optional[str] = None
        if (
            prev_selected_index is not None
            and prev_selected_index >= 0
            and prev_selected_index < len(self._items)
        ):
            prev_selected_id = self._items[prev_selected_index].id

        self._items = [self._coerce_item(item) for item in items]
        if not self._items:
            self._selected_index = None
        else:
            if prev_selected_index is None and self._allow_no_selection:
                self._selected_index = None
            else:
                new_index: typing.Optional[int] = None
                if prev_selected_id is not None:
                    for index, item in enumerate(self._items):
                        if item.id == prev_selected_id:
                            new_index = index
                            break
                if new_index is not None:
                    self._selected_index = new_index
                else:
                    self._selected_index = 0
        self._view_offset = 0
        self._sync_view_offset()
        self._mark_dirty()

    def add_item(self, item: SelectItem | typing.Mapping[str, typing.Any]) -> None:
        self._items.append(self._coerce_item(item))
        if len(self._items) == 1:
            self._selected_index = 0
            self._view_offset = 0
        self._sync_view_offset()
        self._mark_dirty()

    def remove_item_by_id(self, item_id: str) -> None:
        index = -1
        for i, item in enumerate(self._items):
            if item.id == item_id:
                index = i
                break
        if index == -1:
            return
        del self._items[index]
        if not self._items:
            self._selected_index = None
            self._view_offset = 0
        else:
            current = self._selected_index
            if current is not None:
                if current >= len(self._items):
                    self._selected_index = len(self._items) - 1
                elif index < current:
                    self._selected_index = current - 1
        self._sync_view_offset()
        self._mark_dirty()

    def clear_items(self) -> None:
        if not self._items:
            return
        self._items.clear()
        self._selected_index = None
        self._view_offset = 0
        self._mark_dirty()

    def set_selected_index(self, index: int | None) -> None:
        if not self._items:
            return
        if index is None:
            if not self._allow_no_selection:
                return
            if self._selected_index is None:
                return
            self._selected_index = None
            self._sync_view_offset()
            self._mark_dirty()
            return
        if index < 0:
            index = 0
        if index >= len(self._items):
            index = len(self._items) - 1
        if self._selected_index is not None and index == self._selected_index:
            return
        self._selected_index = index
        self._sync_view_offset()
        self._mark_dirty()

    def subscribe_select(
        self, subscriber: typing.Callable[[SelectItem | None], None]
    ) -> None:
        self._select_subscribers.append(subscriber)

    def select_current(self) -> None:
        item = self.selected_item
        for subscriber in list(self._select_subscribers):
            subscriber(item)

    def cancel(self) -> None:
        for subscriber in list(self._select_subscribers):
            subscriber(None)

    def move_selection_up(self) -> None:
        if not self._items:
            return
        if not self._allow_no_selection:
            if self._selected_index == 0:
                return
            self._selected_index = (
                0 if self._selected_index is None else self._selected_index - 1
            )
        else:
            if self._selected_index is None:
                self._selected_index = len(self._items) - 1
            elif self._selected_index == 0:
                self._selected_index = None
            else:
                self._selected_index -= 1
        self._sync_view_offset()
        self._mark_dirty()

    def move_selection_down(self) -> None:
        if not self._items:
            return
        if not self._allow_no_selection:
            if self._selected_index is None:
                self._selected_index = 0
            elif self._selected_index >= len(self._items) - 1:
                return
            else:
                self._selected_index += 1
        else:
            if self._selected_index is None:
                self._selected_index = 0
            elif self._selected_index >= len(self._items) - 1:
                self._selected_index = None
            else:
                self._selected_index += 1
        self._sync_view_offset()
        self._mark_dirty()

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        total = len(self._items)
        start = self._view_offset
        end = start + MAX_VISIBLE_ITEMS
        visible_items = self._items[start:end]
        lines: tui_base.Lines = []
        selected_index = self._selected_index
        for index, item in enumerate(visible_items):
            text = rich_text.Text(item.text)
            item_lines = console.render_lines(
                text,
                options=options,
                pad=False,
                new_lines=False,
            )
            is_selected = (
                total > 0
                and selected_index is not None
                and (start + index) == selected_index
            )
            if is_selected:
                highlighted_lines: tui_base.Lines = []
                for line in item_lines:
                    new_line: list[rich_segment.Segment] = []
                    for segment in line:
                        base_style = segment.style
                        if isinstance(base_style, rich_style.Style):
                            style = base_style + SELECTED_STYLE
                        else:
                            style = SELECTED_STYLE
                        new_line.append(rich_segment.Segment(segment.text, style=style))
                    highlighted_lines.append(new_line)
                lines.extend(highlighted_lines)
            else:
                for line in item_lines:
                    lines.append(list(line))
        hint: str
        if total == 0:
            hint = "No items (0)"
        elif total <= MAX_VISIBLE_ITEMS:
            if total == 1:
                hint = "1 item"
            else:
                hint = f"{total} items"
        else:
            visible_count = len(visible_items)
            hint = f"Showing {visible_count} of {total} items"
        hint_text = rich_text.Text(hint, style="dim")
        hint_lines = console.render_lines(
            hint_text,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines.extend(typing.cast(tui_base.Lines, hint_lines))
        segments: list[rich_segment.Segment] = []
        for line in lines:
            segments.extend(line)
            segments.append(rich_segment.Segment.line())

        if not segments:
            return []

        renderable = rich_segment.Segments(segments)
        styled = self.apply_style(renderable)
        styled_lines = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        return typing.cast(tui_base.Lines, styled_lines)

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        if event.action != "down":
            return
        if event.key == "up":
            self.move_selection_up()
        elif event.key == "down":
            self.move_selection_down()
        elif event.key == "enter":
            self.select_current()
        elif event.key in ("esc", "escape"):
            self.cancel()

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        return

    def _coerce_item(
        self,
        item: SelectItem | typing.Mapping[str, typing.Any],
    ) -> SelectItem:
        if isinstance(item, SelectItem):
            return item
        return SelectItem.model_validate(item)

    def _sync_view_offset(self) -> None:
        if not self._items:
            self._view_offset = 0
            return
        max_offset = max(len(self._items) - MAX_VISIBLE_ITEMS, 0)
        if self._view_offset > max_offset:
            self._view_offset = max_offset
        index = self._selected_index
        if index is None:
            return
        if index < self._view_offset:
            self._view_offset = index
        elif index >= self._view_offset + MAX_VISIBLE_ITEMS:
            self._view_offset = index - MAX_VISIBLE_ITEMS + 1
