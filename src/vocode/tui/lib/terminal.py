from __future__ import annotations

from itertools import zip_longest
import asyncio
import enum
import typing
import time

from rich import console as rich_console
from rich import control as rich_control
from rich import segment as rich_segment

from vocode.logger import logger
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib import unicode as tui_unicode
from vocode.tui.lib.input import base as input_base
from vocode.settings.models import TUIOptions
from pydantic import BaseModel, Field


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
    tui: TUIOptions = Field(default_factory=TUIOptions)


class BaseScreen(typing.Protocol):
    def __init__(self, terminal: "Terminal") -> None: ...

    def render(self) -> None: ...


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
        self._animation_components: typing.Set[tui_base.Component] = set()
        self._cache: typing.Dict[tui_base.Component, tui_base.Lines] = {}
        self._width: int | None = None
        self._force_full_render: bool = False
        self._cursor_line: int = 0
        self._input_handler: input_base.InputHandler | None = input_handler
        self._input_task: asyncio.Task[None] | None = None
        self._focus_stack: list[tui_base.Component] = []
        self._settings: TerminalSettings = (
            settings if settings is not None else TerminalSettings()
        )
        self._unicode = tui_unicode.UnicodeManager(self._settings.tui)
        self._auto_render_enabled: bool = self._settings.auto_render
        self._auto_render_suppressed: int = 0
        self._last_auto_render: float | None = None
        self._auto_render_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None
        self._started: bool = False
        self._screens: list[BaseScreen] = []
        self._removed_components: typing.Set[tui_base.Component] = set()
        if self._input_handler is not None:
            self._input_handler.subscribe(self._handle_input_event)

    @property
    def console(self) -> rich_console.Console:
        return self._console

    @property
    def settings(self) -> TerminalSettings:
        return self._settings

    @property
    def unicode(self) -> tui_unicode.UnicodeManager:
        return self._unicode

    @property
    def has_screens(self) -> bool:
        return bool(self._screens)

    @property
    def top_screen(self) -> BaseScreen | None:
        if not self._screens:
            return None
        return self._screens[-1]

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
            self._request_auto_render()

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

        removed = self._removed_components
        n = len(self._components)

        if index >= 0:
            steps = index
            position = n
            last_active_pos: int | None = None
            for pos, existing in enumerate(self._components):
                if existing in removed:
                    continue
                last_active_pos = pos
                if steps == 0:
                    position = pos
                    break
                steps -= 1
            if position == n:
                if last_active_pos is None:
                    position = n
                else:
                    position = last_active_pos + 1
        else:
            steps = -index
            position = n
            for pos in range(n - 1, -1, -1):
                existing = self._components[pos]
                if existing in removed:
                    continue
                position = pos
                if steps == 1:
                    break
                steps -= 1

        component.terminal = self
        self._components.insert(position, component)
        self.notify_component(component)

    def _delete_removed_components(self) -> None:
        removed = self._removed_components
        if not removed:
            return

        self._components = [c for c in self._components if c not in removed]

        self._dirty_components.difference_update(removed)

        for c in removed:
            self._cache.pop(c, None)

        removed.clear()

    def remove_component(self, component: tui_base.Component) -> None:
        if component not in self._components:
            return

        self._removed_components.add(component)
        self.notify_component(component)

        if component in self._animation_components:
            self._animation_components.remove(component)
        if component in self._focus_stack:
            self.remove_focus(component)
        self._id_index.pop(component.id, None)
        component.terminal = None

    def register_animation(self, component: tui_base.Component) -> None:
        if component not in self._components:
            return
        self._animation_components.add(component)

    def deregister_animation(self, component: tui_base.Component) -> None:
        if component in self._animation_components:
            self._animation_components.remove(component)

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

        if self._screens:
            screen = self._screens[-1]
            if isinstance(event, input_base.KeyEvent):
                handler = getattr(screen, "on_key_event", None)
                if handler is not None:
                    handler(event)
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
        if self._screens:
            top = self._screens[-1]
            top.render()
        else:
            self._request_auto_render(force=True)

    def _set_cursor_line(self, line):
        height = self._console.size.height
        self._cursor_line = min(line, height)

    def _cancel_auto_render_task(self) -> None:
        task = self._auto_render_task
        if task is not None and not task.done():
            task.cancel()
        self._auto_render_task = None

    def _animation_tick(self) -> None:
        if not self._animation_components:
            return

        with self.suspend_auto_render():
            for component in self._animation_components:
                if not component.is_visible:
                    continue
                self.notify_component(component)

    async def _animation_worker(self) -> None:
        try:
            while self._started:
                self._animation_tick()
                await asyncio.sleep(0.1)
        finally:
            self._animation_task = None

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
        if not self._components and not self._screens:
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
            tui_controls.CustomControl.enable_bracketed_paste(),
        )
        self._started = True
        self._request_auto_render(force=True)
        loop = asyncio.get_running_loop()
        if self._animation_task is None or self._animation_task.done():
            self._animation_task = loop.create_task(self._animation_worker())
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
        animation_task = self._animation_task
        if animation_task is not None and not animation_task.done():
            animation_task.cancel()
        self._animation_task = None
        self._console.control(
            tui_controls.CustomControl.disable_bracketed_paste(),
        )
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

    # Screens
    def push_screen(self, screen: BaseScreen) -> None:
        was_empty = not self._screens
        self._screens.append(screen)
        if was_empty:
            self.disable_auto_render()
            self._console.control(tui_controls.CustomControl.enter_alt_screen())
        screen.render()

    def pop_screen(self) -> BaseScreen | None:
        if not self._screens:
            return None
        screen = self._screens.pop()
        if not self._screens:
            self._console.control(tui_controls.CustomControl.exit_alt_screen())
            self.enable_auto_render()
            self._request_auto_render(force=True)
        else:
            top = self._screens[-1]
            top.render()
        return screen

    def _full_render(self, *, repaint_all: bool = False) -> None:
        if self._screens:
            self._console.control(tui_controls.CustomControl.sync_update_start())
            self._console.control(
                tui_controls.CustomControl.full_clear(),
            )
            self._console.control(tui_controls.CustomControl.sync_update_end())
            top = self._screens[-1]
            top.render()
            return

        self._delete_removed_components()

        options = self._console.options
        components_to_render: typing.Iterable[tui_base.Component]
        if repaint_all:
            components_to_render = self._components
        else:
            components_to_render = self._dirty_components

        for component in components_to_render:
            if component.is_hidden:
                self._cache[component] = []
                continue
            lines = component.render(options)
            self._cache[component] = lines

        max_lines = self._settings.tui.full_refresh_max_lines
        all_lines: tui_base.Lines
        if max_lines is None:
            all_lines = []
            for component in self._components:
                lines = self._cache.get(component, [])
                all_lines.extend(lines)
        else:
            chunks: list[tui_base.Lines] = []
            remaining = max_lines
            for component in reversed(self._components):
                if remaining <= 0:
                    break
                lines = self._cache.get(component, [])
                if not lines:
                    continue
                if len(lines) >= remaining:
                    chunks.append(lines[-remaining:])
                    remaining = 0
                    break
                chunks.append(lines)
                remaining -= len(lines)

            all_lines = []
            for chunk in reversed(chunks):
                all_lines.extend(chunk)

        for component in self._components:
            component.is_visible = False

        height = self._console.size.height
        visible_height = min(height, len(all_lines))
        if visible_height > 0:
            covered = 0
            for component in reversed(self._components):
                if covered >= visible_height:
                    break
                component.is_visible = True
                lines = self._cache.get(component, [])
                covered += len(lines)

        self._console.control(tui_controls.CustomControl.sync_update_start())
        self._console.control(
            tui_controls.CustomControl.full_clear(),
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

        # Remove components for good
        self._delete_removed_components()

        # Get a list of all components starting with the top-most changed
        tail_components = self._components[start_index:]
        if not tail_components:
            return True

        lines_to_output: tui_base.Lines = []

        # Special optimization for top-most component - we only need to render its changed lines
        top_component = tail_components[0]
        top_old_lines = self._cache.get(top_component, [])
        if top_component.is_hidden:
            top_new_lines: tui_base.Lines = []
            self._cache[top_component] = []
        else:
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
            if component.is_hidden:
                new_lines = []
                self._cache[component] = []
            elif component in changed_components or cached_lines is None:
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
                    line_width = 0
                    for segment in line:
                        line_width += segment.cell_length
                    if line_width < width:
                        new_line = [tui_controls.CustomControl.erase_line_end().segment]
                        new_line.extend(line)
                    else:
                        new_line = list(line)
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
        if self._screens:
            self._full_render()
            self._width = self._console.size.width
            self._dirty_components.clear()
            return

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

        repaint_all = self._width is not None and self._width != width

        if self._width is None or self._force_full_render or self._width != width:
            self._full_render(repaint_all=repaint_all)
        else:
            handled = self._incremental_render(changed_components)
            if not handled:
                self._full_render()

        self._width = width
        self._force_full_render = False
        self._dirty_components.clear()
