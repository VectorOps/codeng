from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import zip_longest
import asyncio
import typing

from rich import console as rich_console
from rich import control as rich_control
from rich import segment as rich_segment

from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib.input import base as input_base


SYNC_UPDATE_START: typing.Final[str] = tui_controls.SYNC_UPDATE_START
SYNC_UPDATE_END: typing.Final[str] = tui_controls.SYNC_UPDATE_END
ERASE_SCROLLBACK: typing.Final[str] = tui_controls.ERASE_SCROLLBACK
ERASE_SCREEN: typing.Final[str] = tui_controls.ERASE_SCREEN
CURSOR_HOME: typing.Final[str] = tui_controls.CURSOR_HOME
CURSOR_COLUMN_1: typing.Final[str] = tui_controls.CURSOR_COLUMN_1
ERASE_DOWN: typing.Final[str] = tui_controls.ERASE_DOWN
CURSOR_PREVIOUS_LINE_FMT: typing.Final[str] = tui_controls.CURSOR_PREVIOUS_LINE_FMT


Lines = typing.List[typing.List[rich_segment.Segment]]


class Component(ABC):
    def __init__(self, id: str | None = None) -> None:
        self.id = id
        self.terminal: Terminal | None = None

    @abstractmethod
    def render(self) -> Lines:
        raise NotImplementedError

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        pass

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        pass


class Terminal:
    def __init__(
        self,
        console: rich_console.Console | None = None,
        input_handler: input_base.InputHandler | None = None,
    ) -> None:
        self._console: rich_console.Console = (
            console if console is not None else rich_console.Console()
        )
        self._components: typing.List[Component] = []
        self._id_index: typing.Dict[str, Component] = {}
        self._dirty_components: typing.Set[Component] = set()
        self._cache: typing.Dict[Component, Lines] = {}
        self._width: int | None = None
        self._force_full_render: bool = False
        self._cursor_line: int = 0
        self._input_handler: input_base.InputHandler | None = input_handler
        self._input_task: asyncio.Task[None] | None = None
        self._focus_stack: list[Component] = []
        if self._input_handler is not None:
            self._input_handler.subscribe(self._handle_input_event)

    @property
    def console(self) -> rich_console.Console:
        return self._console

    def append_component(self, component: Component) -> None:
        if component in self._components:
            return
        if component.id is not None:
            if component.id in self._id_index:
                raise ValueError(f"Component id already exists: {component.id}")
            self._id_index[component.id] = component
        component.terminal = self
        self._components.append(component)
        self.notify_component(component)

    def insert_component(self, index: int, component: Component) -> None:
        if component in self._components:
            return
        if component.id is not None:
            if component.id in self._id_index:
                raise ValueError(f"Component id already exists: {component.id}")
            self._id_index[component.id] = component
        length = len(self._components)
        if index >= 0:
            position = index
            if position > length:
                position = length
        else:
            position = length + index
            if position < 0:
                position = 0
        component.terminal = self
        self._components.insert(position, component)
        self.notify_component(component)
        self._force_full_render = True

    def remove_component(self, component: Component) -> None:
        if component not in self._components:
            return
        self._components.remove(component)
        if component in self._dirty_components:
            self._dirty_components.remove(component)
        if component in self._cache:
            del self._cache[component]
        for key, value in list(self._id_index.items()):
            if value is component:
                del self._id_index[key]
        if component in self._focus_stack:
            self.remove_focus(component)
        component.terminal = None
        self._force_full_render = True

    def notify_component(self, component: Component) -> None:
        if component in self._components:
            self._dirty_components.add(component)

    def get_component(self, component_id: str) -> Component:
        if component_id not in self._id_index:
            raise KeyError(component_id)
        return self._id_index[component_id]

    def push_focus(self, component: Component) -> None:
        if component not in self._components:
            return
        if component in self._focus_stack:
            self._focus_stack.remove(component)
        self._focus_stack.append(component)

    def pop_focus(self) -> Component | None:
        if not self._focus_stack:
            return None
        return self._focus_stack.pop()

    def remove_focus(self, component: Component) -> None:
        if not self._focus_stack:
            return
        self._focus_stack = [c for c in self._focus_stack if c is not component]

    def _print_lines(self, lines: Lines) -> None:
        if not lines:
            return

        size = self._console.size
        height = size.height
        if height <= 0:
            return

        batched: typing.List[rich_segment.Segment] = []
        for line in lines:
            batched.extend(line)
            batched.append(rich_segment.Segment.line())

        if batched:
            self._console.print(rich_segment.Segments(batched), end="")

    def _handle_input_event(self, event: input_base.InputEvent) -> None:
        if not self._focus_stack:
            return
        component = self._focus_stack[-1]
        if isinstance(event, input_base.KeyEvent):
            component.on_key_event(event)
        elif isinstance(event, input_base.MouseEvent):
            component.on_mouse_event(event)

    def _set_cursor_line(self, line):
        height = self._console.size.height
        self._cursor_line = min(line, height)

    def render(self) -> None:
        if not self._components:
            return

        size = self._console.size
        width = size.width
        height = size.height

        if width <= 0 or height <= 0:
            return

        changed_components = set(self._dirty_components)

        if (
            not changed_components
            and not self._force_full_render
            and self._width == width
        ):
            return

        if self._width is None or self._force_full_render or self._width != width:
            self._full_render()
        else:
            handled = self._incremental_render(changed_components)
            if not handled:
                self._full_render()

        self._width = width
        self._force_full_render = False
        self._dirty_components.clear()

    async def start(self) -> None:
        self._console.control(
            rich_control.Control.clear(),
            rich_control.Control.home(),
        )
        if self._input_handler is None:
            return
        if self._input_task is not None and not self._input_task.done():
            return
        loop = asyncio.get_running_loop()
        self._input_task = loop.create_task(self._input_handler.run())
        await asyncio.sleep(0)

    async def stop(self) -> None:
        task = self._input_task
        if task is None:
            return
        self._input_task = None
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def run(self) -> None:
        if self._input_handler is None:
            raise RuntimeError("Terminal has no input handler")

        async def _main() -> None:
            await self.start()
            task = self._input_task
            if task is None:
                return
            try:
                await task
            finally:
                self._input_task = None

        asyncio.run(_main())

    def _full_render(self) -> None:
        new_cache: typing.Dict[Component, Lines] = {}
        for component in self._components:
            lines = component.render()
            new_cache[component] = lines

        all_lines: Lines = []
        for component in self._components:
            lines = new_cache.get(component, [])
            all_lines.extend(lines)
        self._console.control(tui_controls.CustomControl.sync_update_start())
        self._console.control(
            tui_controls.CustomControl.erase_scrollback(),
            rich_control.Control.clear(),
            rich_control.Control.home(),
        )
        self._print_lines(all_lines)
        self._set_cursor_line(len(all_lines))
        self._console.control(tui_controls.CustomControl.sync_update_end())

        self._cache = new_cache

    def _incremental_render(self, changed_components: typing.Set[Component]) -> bool:
        if not changed_components:
            return True

        size = self._console.size
        height = size.height
        if height <= 0:
            return False

        remaining = set(changed_components)
        row = self._cursor_line
        start_index = 0

        # Walk components backward and record their height to find a top-most visible component row
        for index in range(len(self._components) - 1, -1, -1):
            if not remaining:
                break

            start_index = index
            component = self._components[index]
            cached_lines = self._cache.get(component)
            is_new = cached_lines is None

            if component in remaining:
                remaining.remove(component)

            if not is_new:
                row -= len(cached_lines)

            if row < 0:
                break

        # We stopped the walk, but there are dirty components remaining - need a full repaint
        if remaining:
            return False

        # Get a list of all components starting with the top-most changed
        tail_components = self._components[start_index:]
        if not tail_components:
            return True

        lines_to_output: Lines = []

        # Special optimization for top-most component - we only need to render its changed lines
        top_component = tail_components[0]
        top_old_lines = self._cache.get(top_component, [])
        top_new_lines = top_component.render()
        self._cache[top_component] = top_new_lines

        top_mismatch = 0
        for i, (x, y) in enumerate(zip_longest(top_old_lines, top_new_lines)):
            if x != y:
                top_mismatch = i
                break

        lines_to_output.extend(top_new_lines[top_mismatch:])
        row += top_mismatch

        # If row is negative, then we have offscreen change and need to repaint
        if row < 0:
            return False

        # Render remaining components
        for component in tail_components[1:]:
            if component in changed_components or component not in self._cache:
                new_lines = component.render()
                self._cache[component] = new_lines

            lines = self._cache.get(component, [])
            lines_to_output.extend(lines)

        self._console.control(
            tui_controls.CustomControl.sync_update_start(),
            tui_controls.CustomControl.cursor_column_1(),
        )

        if row != self._cursor_line:
            self._console.control(
                tui_controls.CustomControl.cursor_previous_line(
                    self._cursor_line - row
                ),
                tui_controls.CustomControl.erase_down(),
            )
        self._print_lines(lines_to_output)
        self._set_cursor_line(row + len(lines_to_output))
        self._console.control(tui_controls.CustomControl.sync_update_end())

        return True
