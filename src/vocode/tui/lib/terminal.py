from __future__ import annotations

from itertools import zip_longest
import asyncio
import enum
import typing
import time

from rich import console as rich_console
from rich import control as rich_control
from rich import segment as rich_segment

from vocode.tui.lib import base as tui_base
from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib.input import base as input_base
from pydantic import BaseModel


class IncrementalRenderMode(str, enum.Enum):
    PADDING = "padding"
    CLEAR_TO_BOTTOM = "clear_to_bottom"


class _SuspendAutoRender:
    def __init__(self, terminal: Terminal) -> None:
        self._terminal = terminal

    def __enter__(self) -> None:
        self._terminal.disable_auto_render()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._terminal.enable_auto_render()


class TerminalSettings(BaseModel):
    auto_render: bool = True
    min_render_interval_ms: int = 50
    incremental_mode: IncrementalRenderMode = IncrementalRenderMode.PADDING


class Terminal:
    def __init__(
        self,
        console: rich_console.Console | None = None,
        input_handler: input_base.InputHandler | None = None,
        settings: TerminalSettings | None = None,
    ) -> None:
        self._console: rich_console.Console = (
            console if console is not None else rich_console.Console()
        )
        self._components: typing.List[tui_base.Component] = []
        self._id_index: typing.Dict[str, tui_base.Component] = {}
        self._dirty_components: typing.Set[tui_base.Component] = set()
        self._cache: typing.Dict[Component, Lines] = {}
        self._width: int | None = None
        self._force_full_render: bool = False
        self._cursor_line: int = 0
        self._input_handler: input_base.InputHandler | None = input_handler
        self._input_task: asyncio.Task[None] | None = None
        self._focus_stack: list[tui_base.Component] = []
        self._settings: TerminalSettings = (
            settings if settings is not None else TerminalSettings()
        )
        self._auto_render_enabled: bool = self._settings.auto_render
        self._auto_render_suppressed: int = 0
        self._last_auto_render: float | None = None
        self._auto_render_task: asyncio.Task[None] | None = None
        self._started: bool = False
        if self._input_handler is not None:
            self._input_handler.subscribe(self._handle_input_event)

    @property
    def console(self) -> rich_console.Console:
        return self._console

    def disable_auto_render(self) -> None:
        if self._auto_render_suppressed == 0:
            self._cancel_auto_render_task()
        self._auto_render_suppressed += 1
        self._auto_render_enabled = False

    def enable_auto_render(self) -> None:
        if self._auto_render_suppressed > 0:
            self._auto_render_suppressed -= 1
        if self._auto_render_suppressed == 0:
            self._auto_render_enabled = True
            self._request_auto_render(force=True)

    def suspend_auto_render(self) -> typing.ContextManager[None]:
        return _SuspendAutoRender(self)

    # Components
    @property
    def components(self):
        return self._components

    def append_component(self, component: tui_base.Component) -> None:
        if component in self._components:
            return
        if component.id is not None:
            if component.id in self._id_index:
                raise ValueError(f"Component id already exists: {component.id}")
            self._id_index[component.id] = component
        component.terminal = self
        self._components.append(component)
        self.notify_component(component)

    def insert_component(self, index: int, component: tui_base.Component) -> None:
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

    def remove_component(self, component: tui_base.Component) -> None:
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

    def notify_component(self, component: tui_base.Component) -> None:
        if component in self._components:
            self._dirty_components.add(component)
            self._request_auto_render()

    def get_component(self, component_id: str) -> tui_base.Component:
        if component_id not in self._id_index:
            raise KeyError(component_id)
        return self._id_index[component_id]

    # Focus
    def push_focus(self, component: tui_base.Component) -> None:
        if component not in self._components:
            return
        if component in self._focus_stack:
            self._focus_stack.remove(component)
        self._focus_stack.append(component)

    def pop_focus(self) -> tui_base.Component | None:
        if not self._focus_stack:
            return None
        return self._focus_stack.pop()

    def remove_focus(self, component: tui_base.Component) -> None:
        if not self._focus_stack:
            return
        self._focus_stack = [c for c in self._focus_stack if c is not component]

    # Rendering
    def _print_lines(self, lines: tui_base.Lines) -> None:
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
        if isinstance(event, input_base.ResizeEvent):
            self._handle_resize_event(event)
            return

        if not self._focus_stack:
            return

        component = self._focus_stack[-1]
        if isinstance(event, input_base.KeyEvent):
            component.on_key_event(event)
        elif isinstance(event, input_base.MouseEvent):
            component.on_mouse_event(event)

    def _handle_resize_event(self, event: input_base.ResizeEvent) -> None:
        self._force_full_render = True
        self._request_auto_render(force=True)

    def _set_cursor_line(self, line):
        height = self._console.size.height
        self._cursor_line = min(line, height)

    def _cancel_auto_render_task(self) -> None:
        task = self._auto_render_task
        if task is not None and not task.done():
            task.cancel()
        self._auto_render_task = None

    async def _auto_render_worker(self, *, force: bool) -> None:
        try:
            if not force:
                interval = max(self._settings.min_render_interval_ms, 0) / 1000.0
                last = self._last_auto_render
                if last is not None:
                    now = time.monotonic()
                    elapsed = now - last
                    if elapsed < interval:
                        await asyncio.sleep(interval - elapsed)
            await self.render()
            self._last_auto_render = time.monotonic()
        finally:
            self._auto_render_task = None

    def _request_auto_render(self, *, force: bool = False) -> None:
        if not self._started:
            return
        if not self._auto_render_enabled:
            return
        if not self._components:
            return
        if not self._dirty_components and not self._force_full_render:
            return
        task = self._auto_render_task
        if task is not None and not task.done():
            if not force:
                return
            self._cancel_auto_render_task()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._auto_render_task = loop.create_task(self._auto_render_worker(force=force))

    async def start(self) -> None:
        self._console.control(
            rich_control.Control.clear(),
            rich_control.Control.home(),
        )
        self._started = True
        self._request_auto_render(force=True)
        if self._input_handler is None:
            return
        self._console.control(rich_control.Control.show_cursor(False))
        if self._input_task is not None and not self._input_task.done():
            return
        loop = asyncio.get_running_loop()
        self._input_task = loop.create_task(self._input_handler.run())
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self._started = False
        self._cancel_auto_render_task()
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
        self._console.control(rich_control.Control.show_cursor(True))

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
        options = self._console.options

        for component in self._dirty_components:
            lines = component.render(options)
            self._cache[component] = lines

        all_lines: tui_base.Lines = []
        for component in self._components:
            lines = self._cache.get(component, [])
            all_lines.extend(lines)

            component.is_visible = False

        height = self._console.size.height
        if height > 0:
            covered = 0
            for component in reversed(self._components):
                if covered >= height:
                    break
                component.is_visible = True
                lines = self._cache.get(component, [])
                covered += len(lines)

        self._console.control(tui_controls.CustomControl.sync_update_start())
        self._console.control(
            tui_controls.CustomControl.erase_scrollback(),
            rich_control.Control.clear(),
            rich_control.Control.home(),
        )
        self._print_lines(all_lines)
        self._set_cursor_line(len(all_lines))
        self._console.control(tui_controls.CustomControl.sync_update_end())

    def _incremental_render(
        self, changed_components: typing.Set[tui_base.Component]
    ) -> bool:
        if not changed_components:
            return True

        size = self._console.size
        width = size.width
        height = size.height
        if width <= 0 or height <= 0:
            return False

        options = self._console.options

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

        lines_to_output: tui_base.Lines = []

        # Special optimization for top-most component - we only need to render its changed lines
        top_component = tail_components[0]
        top_old_lines = self._cache.get(top_component, [])
        top_new_lines = top_component.render(options)
        self._cache[top_component] = top_new_lines

        top_mismatch = 0
        top_changed = False
        for i, (x, y) in enumerate(zip_longest(top_old_lines, top_new_lines)):
            if x != y:
                top_mismatch = i
                top_changed = True
                break

        if not top_changed:
            top_mismatch = len(top_new_lines)

        any_changed = top_changed

        lines_to_output.extend(top_new_lines[top_mismatch:])
        row += top_mismatch

        # If row is negative, then we have offscreen change and need to repaint
        if row < 0:
            return False

        # Render remaining components
        for component in tail_components[1:]:
            cached_lines = self._cache.get(component)
            if component in changed_components or cached_lines is None:
                new_lines = component.render(options)
                self._cache[component] = new_lines
            else:
                new_lines = cached_lines

            if cached_lines is None or new_lines != cached_lines:
                any_changed = True

            lines_to_output.extend(new_lines)

        if not any_changed:
            return True
        old_span = self._cursor_line - row
        if old_span < 0:
            old_span = 0

        self._console.control(
            tui_controls.CustomControl.sync_update_start(),
            tui_controls.CustomControl.cursor_column_1(),
        )

        if row != self._cursor_line:
            self._console.control(
                tui_controls.CustomControl.cursor_previous_line(self._cursor_line - row)
            )

        if self._settings.incremental_mode is IncrementalRenderMode.CLEAR_TO_BOTTOM:
            self._console.control(tui_controls.CustomControl.erase_down())
            self._print_lines(lines_to_output)
            new_cursor_line = row + len(lines_to_output)
        else:
            if lines_to_output:
                cleared_lines: tui_base.Lines = []
                for line in lines_to_output:
                    new_line = [tui_controls.CustomControl.erase_line_end().segment]
                    new_line.extend(line)
                    cleared_lines.append(new_line)

                self._print_lines(cleared_lines)
            new_cursor_line = row + len(lines_to_output)
            self._console.control(tui_controls.CustomControl.erase_down())

        self._set_cursor_line(new_cursor_line)
        self._console.control(tui_controls.CustomControl.sync_update_end())

        # Update component visibility using a conservative bottom-up band and cleanup
        n_components = len(self._components)
        prev_first_visible = None
        for index in range(n_components - 1, -1, -1):
            if not self._components[index].is_visible:
                break
            prev_first_visible = index

        visible_budget = self._cursor_line
        new_first_visible = None
        for index in range(n_components - 1, -1, -1):
            if visible_budget <= 0:
                break

            component = self._components[index]
            cached_lines = self._cache.get(component, ())
            line_count = len(cached_lines)

            if line_count <= 0:
                component.is_visible = False
                continue

            component.is_visible = True
            visible_budget -= line_count
            new_first_visible = index

        if prev_first_visible is not None and new_first_visible is not None:
            if prev_first_visible < new_first_visible:
                for index in range(prev_first_visible, new_first_visible):
                    self._components[index].is_visible = False

        return True

    async def render(self) -> None:
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

        # self._force_full_render = True

        if self._width is None or self._force_full_render or self._width != width:
            self._full_render()
        else:
            handled = self._incremental_render(changed_components)
            if not handled:
                self._full_render()

        self._width = width
        self._force_full_render = False
        self._dirty_components.clear()
