from __future__ import annotations

import typing

from pydantic import BaseModel
from rich import console as rich_console
from rich import markdown as rich_markdown
from rich import segment as rich_segment
from rich import style as rich_style
from rich import text as rich_text

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.input import base as input_base


Lines = tui_terminal.Lines
MAX_VISIBLE_ITEMS: typing.Final[int] = 5
SELECTED_STYLE: typing.Final[rich_style.Style] = rich_style.Style(reverse=True)


class SelectItem(BaseModel):
    id: str
    text: str


class SelectListComponent(tui_terminal.Component):
    def __init__(
        self,
        items: typing.Iterable[SelectItem | typing.Mapping[str, typing.Any]] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._items: list[SelectItem] = []
        self._selected_index: int = 0
        self._view_offset: int = 0
        self._select_subscribers: list[typing.Callable[[SelectItem], None]] = []
        if items is not None:
            self.set_items(items)

    @property
    def items(self) -> list[SelectItem]:
        return list(self._items)

    @property
    def selected_index(self) -> int | None:
        if not self._items:
            return None
        return self._selected_index

    @property
    def selected_item(self) -> SelectItem | None:
        if not self._items:
            return None
        index = self._selected_index
        if index < 0 or index >= len(self._items):
            return None
        return self._items[index]

    def set_items(
        self,
        items: typing.Iterable[SelectItem | typing.Mapping[str, typing.Any]],
    ) -> None:
        self._items = [self._coerce_item(item) for item in items]
        if self._items:
            self._selected_index = 0
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
            self._selected_index = 0
            self._view_offset = 0
        else:
            if self._selected_index >= len(self._items):
                self._selected_index = len(self._items) - 1
            elif index < self._selected_index:
                self._selected_index -= 1
        self._sync_view_offset()
        self._mark_dirty()

    def clear_items(self) -> None:
        if not self._items:
            return
        self._items.clear()
        self._selected_index = 0
        self._view_offset = 0
        self._mark_dirty()

    def set_selected_index(self, index: int) -> None:
        if not self._items:
            return
        if index < 0:
            index = 0
        if index >= len(self._items):
            index = len(self._items) - 1
        if index == self._selected_index:
            return
        self._selected_index = index
        self._sync_view_offset()
        self._mark_dirty()

    def subscribe_select(self, subscriber: typing.Callable[[SelectItem], None]) -> None:
        self._select_subscribers.append(subscriber)

    def select_current(self) -> None:
        item = self.selected_item
        if item is None:
            return
        for subscriber in list(self._select_subscribers):
            subscriber(item)

    def move_selection_up(self) -> None:
        if not self._items:
            return
        if self._selected_index == 0:
            return
        self._selected_index -= 1
        self._sync_view_offset()
        self._mark_dirty()

    def move_selection_down(self) -> None:
        if not self._items:
            return
        if self._selected_index >= len(self._items) - 1:
            return
        self._selected_index += 1
        self._sync_view_offset()
        self._mark_dirty()

    def render(self, options: rich_console.ConsoleOptions) -> Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        total = len(self._items)
        start = self._view_offset
        end = start + MAX_VISIBLE_ITEMS
        visible_items = self._items[start:end]
        width = options.max_width or console.width
        lines: Lines = []
        for index, item in enumerate(visible_items):
            markdown = rich_markdown.Markdown(item.text)
            item_lines = console.render_lines(
                markdown,
                options=options,
                pad=False,
                new_lines=False,
            )
            is_selected = total > 0 and (start + index) == self._selected_index
            if is_selected:
                highlighted_lines: Lines = []
                for line in item_lines:
                    current_len = 0
                    new_line: list[rich_segment.Segment] = []
                    for segment in line:
                        text = segment.text
                        current_len += len(text)
                        base_style = segment.style
                        if isinstance(base_style, rich_style.Style):
                            style = base_style + SELECTED_STYLE
                        else:
                            style = SELECTED_STYLE
                        new_line.append(rich_segment.Segment(text, style=style))
                    if width > 0 and current_len < width:
                        pad = width - current_len
                        if pad > 0:
                            new_line.append(
                                rich_segment.Segment(" " * pad, style=SELECTED_STYLE)
                            )
                    highlighted_lines.append(new_line)
                lines.extend(highlighted_lines)
            else:
                padded_lines: Lines = []
                for line in item_lines:
                    current_len = sum(len(segment.text) for segment in line)
                    if width > 0 and current_len < width:
                        pad = width - current_len
                        if pad > 0:
                            new_line = list(line)
                            new_line.append(rich_segment.Segment(" " * pad))
                            padded_lines.append(new_line)
                            continue
                    padded_lines.append(list(line))
                lines.extend(padded_lines)
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
        lines.extend(typing.cast(Lines, hint_lines))
        return lines

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        if event.action != "down":
            return
        if event.key == "up":
            self.move_selection_up()
        elif event.key == "down":
            self.move_selection_down()
        elif event.key == "enter":
            self.select_current()

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        return

    def _coerce_item(
        self,
        item: SelectItem | typing.Mapping[str, typing.Any],
    ) -> SelectItem:
        if isinstance(item, SelectItem):
            return item
        return SelectItem.model_validate(item)

    def _mark_dirty(self) -> None:
        terminal = self.terminal
        if terminal is not None:
            terminal.notify_component(self)

    def _sync_view_offset(self) -> None:
        if not self._items:
            self._view_offset = 0
            return
        max_offset = max(len(self._items) - MAX_VISIBLE_ITEMS, 0)
        if self._view_offset > max_offset:
            self._view_offset = max_offset
        if self._selected_index < self._view_offset:
            self._view_offset = self._selected_index
        elif self._selected_index >= self._view_offset + MAX_VISIBLE_ITEMS:
            self._view_offset = self._selected_index - MAX_VISIBLE_ITEMS + 1
