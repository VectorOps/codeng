from __future__ import annotations

import asyncio
import dataclasses
import enum
import typing
from rich import console as rich_console
from rich import control as rich_control
from rich import align as rich_align
from rich import text as rich_text
import pyfiglet
from vocode import state as vocode_state
from vocode import models as vocode_models
from vocode import settings as vocode_settings
from vocode.logger import logger
from vocode.manager import proto as manager_proto
from vocode.tui import lib as tui_terminal
from vocode.tui import styles as tui_styles
from vocode.tui import history as tui_history
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.components import composite_component as tui_composite_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.components import rich_text_component as tui_rich_text_component
from vocode.tui.lib.components import renderable as tui_renderable_component
from vocode.tui.lib.components import step_output_component as tui_step_output_component
from vocode.tui.lib.components import select_list as tui_select_list
from vocode.tui.components import command_manager_help as command_manager_help_component
from vocode.tui.components import progress as progress_component
from vocode.tui.components import tool_call_req as tool_call_req_component
from vocode.tui.components import toolbar as toolbar_component
from vocode.tui import command_manager as tui_command_manager
from vocode.tui.lib.input import base as input_base
from vocode.tui.lib.input import handler as input_handler_mod


AUTOCOMPLETE_DEBOUNCE_MS: typing.Final[int] = 100
PROGRESS_VISIBILITY_DELAY_S: typing.Final[float] = 2.0
HISTORY_SEARCH_MAX_ITEMS: typing.Final[int] = 20


class ActionKind(str, enum.Enum):
    DEFAULT = "default"
    AUTOCOMPLETE = "autocomplete"
    COMMAND_MANAGER = "command_manager"
    HISTORY_SEARCH = "history_search"


@dataclasses.dataclass
class ActionItem:
    kind: ActionKind
    component: tui_terminal.Component
    animated: bool = False


class TUIState:
    def __init__(
        self,
        on_input: typing.Callable[[str], typing.Awaitable[None]],
        console: rich_console.Console | None = None,
        input_handler: input_base.InputHandler | None = None,
        on_autocomplete_request: (
            typing.Callable[[str, int, int], typing.Awaitable[None]] | None
        ) = None,
        on_open_logs: typing.Callable[[], typing.Awaitable[None]] | None = None,
        on_stop: typing.Callable[[], typing.Awaitable[None]] | None = None,
        on_eof: typing.Callable[[], typing.Awaitable[None]] | None = None,
        tui_options: vocode_settings.TUIOptions | None = None,
    ) -> None:
        self._on_input = on_input
        self._on_autocomplete_request = on_autocomplete_request
        self._on_open_logs = on_open_logs
        self._on_stop = on_stop
        self._on_eof = on_eof
        self._markdown_render_mode = (
            tui_markdown_component.MarkdownRenderMode.RICH_MARKDOWN
        )
        if tui_options is not None:
            if (
                tui_options.markdown_render_mode
                is vocode_settings.MarkdownRenderMode.syntax
            ):
                self._markdown_render_mode = (
                    tui_markdown_component.MarkdownRenderMode.SYNTAX
                )
        if input_handler is None:
            input_handler = input_handler_mod.PosixInputHandler()
        self._input_handler = input_handler
        self._input_task: asyncio.Task[None] | None = None
        if tui_options is None:
            settings = tui_terminal.TerminalSettings()
        else:
            settings = tui_terminal.TerminalSettings(tui=tui_options)
        self._terminal = tui_terminal.Terminal(
            console=console,
            settings=settings,
        )

        header = tui_renderable_component.CallbackComponent(
            self._render_banner,
            id="header",
        )
        input_component = tui_input_component.InputComponent(
            "",
            id="input",
            prefix="> ",
            component_style=tui_styles.INPUT_PANEL_COMPONENT_STYLE,
            submit_with_enter=(
                True if tui_options is None else bool(tui_options.submit_with_enter)
            ),
        )

        self._input_component = input_component
        self._history_manager = tui_history.HistoryManager()
        self._input_keymap = self._create_input_keymap()
        self._step_components: dict[
            str, tui_step_output_component.StepOutputComponent
        ] = {}
        self._step_component_ids: set[str] = set()
        self._step_handlers: dict[
            vocode_state.StepType, typing.Callable[[vocode_state.Step], None]
        ] = {
            vocode_state.StepType.OUTPUT_MESSAGE: self._handle_output_message_step,
            vocode_state.StepType.INPUT_MESSAGE: self._handle_input_message_step,
            vocode_state.StepType.REJECTION: self._handle_rejection_step,
            vocode_state.StepType.PROMPT: self._handle_prompt_step,
            vocode_state.StepType.PROMPT_CONFIRM: self._handle_prompt_step,
            vocode_state.StepType.TOOL_REQUEST: self._handle_tool_request_step,
        }

        self._terminal.append_component(header)
        self._terminal.append_component(input_component)
        toolbar = toolbar_component.ToolbarComponent(
            id="toolbar",
            component_style=tui_styles.TOOLBAR_COMPONENT_STYLE,
        )
        self._base_toolbar_component = toolbar
        self._toolbar_component = toolbar
        self._terminal.append_component(toolbar)

        self._progress_component: progress_component.ProgressComponent | None = None

        self._action_stack: list[ActionItem] = [
            ActionItem(kind=ActionKind.DEFAULT, component=toolbar)
        ]

        self._terminal.push_focus(input_component)

        self._input_component.subscribe_submit(self._handle_submit)
        self._input_component.subscribe_cursor_event(self._handle_cursor_event)
        self._input_component.subscribe_change(self._handle_change)

        if self._input_handler is not None:
            self._input_handler.subscribe(self._handle_input_event)

        self._autocomplete_task: asyncio.Task[None] | None = None
        self._last_autocomplete_text: str | None = None
        self._last_autocomplete_row: int | None = None
        self._last_autocomplete_col: int | None = None
        self._autocomplete_pending: bool = False
        self._autocomplete_items: list[manager_proto.AutocompleteItem] | None = None
        self._ui_state: manager_proto.UIServerStatePacket | None = None

        self._progress_by_id: dict[str, manager_proto.ProgressPacket] = {}
        self._progress_visible_by_id: set[str] = set()
        self._progress_gate_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_progress_id: str | None = None

        self._history_search_query: str = ""
        self._history_search_list: typing.Optional[
            tui_select_list.SelectListComponent
        ] = None
        self._history_search_query_view: typing.Optional[
            tui_rich_text_component.RichTextComponent
        ] = None

        self._progressive_hotkey: tui_input_component.KeyBinding | None = None
        self._progressive_count: int = 0

        self._progressive_keybindings: set[tui_input_component.KeyBinding] = {
            tui_input_component.KeyBinding("e"),
            tui_input_component.KeyBinding("c"),
            tui_input_component.KeyBinding("e", shift=True),
            tui_input_component.KeyBinding("c", shift=True),
        }

        self._suppress_history_update: int = 0

    def _render_banner(
        self, console: rich_console.Console
    ) -> rich_console.RenderableType:
        tui_settings = self._terminal.settings.tui
        text = tui_settings.banner_text.strip() if tui_settings.banner_text else ""
        if not text:
            return rich_text.Text("")
        try:
            fig = pyfiglet.Figlet(font=tui_settings.banner_font)
            raw = fig.renderText(text).rstrip("\n")
        except Exception:
            raw = text
        return rich_align.Align(rich_text.Text(raw), align="center")

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
            tui_input_component.KeyBinding("r", ctrl=True): self._handle_history_search,
            tui_input_component.KeyBinding(
                "space", ctrl=True
            ): self._handle_open_command_manager,
            tui_input_component.KeyBinding("c", ctrl=True): self._handle_stop,
            tui_input_component.KeyBinding("d", ctrl=True): self._handle_eof,
        }

    def _handle_input_key_event(self, event: input_base.KeyEvent) -> bool:
        top = self._action_stack[-1]

        if top.kind is ActionKind.HISTORY_SEARCH:
            return self._handle_history_search_key_event(event)

        if top.kind is ActionKind.AUTOCOMPLETE:
            component = typing.cast(tui_select_list.SelectListComponent, top.component)
            return self._handle_select_list_key_event(
                component,
                event,
                allow_history_fallback=True,
            )

        if top.kind is ActionKind.COMMAND_MANAGER:
            if event.action != "down":
                return True
            if event.key == "space" and event.ctrl:
                self._pop_action(ActionKind.COMMAND_MANAGER)
                return True
            if event.key in ("esc", "escape"):
                self._pop_action(ActionKind.COMMAND_MANAGER)
                return True

            # TODO: Optimize
            hotkeys = self._build_command_manager_hotkeys()
            binding = tui_input_component.KeyBinding(
                key=event.key,
                ctrl=event.ctrl,
                alt=event.alt,
                shift=event.shift,
            )
            for hotkey in hotkeys:
                if hotkey.mapping == binding:
                    handled = hotkey.handler(event)
                    self._pop_action(ActionKind.COMMAND_MANAGER)
                    return handled
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

    def _history_search_set_query(self, query: str) -> None:
        self._history_search_query = query
        query_view = self._history_search_query_view
        if query_view is not None:
            query_view.text = f"History search: {query}"
        self._history_search_update_items()

    def _history_search_update_items(self) -> None:
        component = self._history_search_list
        if component is None:
            return
        query = self._history_search_query.strip()
        entries = list(self._history_manager.entries)
        items: list[dict[str, str]] = []
        q = query.casefold()
        for index, entry in enumerate(reversed(entries)):
            if q and q not in entry.casefold():
                continue
            single_line_entry = entry.splitlines()[0] if entry.splitlines() else ""
            items.append(
                {
                    "id": str(index),
                    "text": single_line_entry,
                    "value": entry,
                }
            )
            if len(items) >= HISTORY_SEARCH_MAX_ITEMS:
                break
        component.set_items(items)

    def _history_search_cancel(self) -> None:
        self._history_search_query = ""
        self._history_search_list = None
        self._history_search_query_view = None
        self._pop_action(ActionKind.HISTORY_SEARCH)

    def _history_search_accept(self, value: str) -> None:
        component = self._input_component
        self._suppress_history_update += 1
        try:
            component.text = value
            lines = component.lines
            if lines:
                last_row = len(lines) - 1
                last_col = len(lines[last_row])
                component.set_cursor_position(last_row, last_col)
        finally:
            self._suppress_history_update -= 1
        self._history_search_cancel()

    def _handle_history_search_key_event(self, event: input_base.KeyEvent) -> bool:
        if event.action != "down":
            return True
        key = event.key
        if key in ("esc", "escape") or (key == "g" and event.ctrl):
            self._history_search_cancel()
            return True
        if key == "r" and event.ctrl:
            self._history_search_cancel()
            return True
        if key == "backspace":
            if self._history_search_query:
                self._history_search_set_query(self._history_search_query[:-1])
            return True
        if key == "tab" and not event.ctrl and not event.alt:
            if self._history_search_list is None:
                self._history_search_cancel()
                return True
            items = self._history_search_list.items
            if not items:
                self._history_search_cancel()
                return True
            if self._history_search_list.selected_index is None:
                if len(items) == 1:
                    self._history_search_accept(items[0].text)
                    return True
                self._history_search_list.set_selected_index(0)
                return True
            selected = self._history_search_list.selected_item
            if selected is None or selected.value is None:
                self._history_search_cancel()
                return True
            self._history_search_accept(selected.value)
            return True
        if key == "enter":
            if self._history_search_list is None:
                self._history_search_cancel()
                return False
            selected = self._history_search_list.selected_item
            if selected is None or selected.value is None:
                self._history_search_cancel()
                return False
            self._history_search_accept(selected.value)
            return True

        if key in ("up", "down") or (key in ("n", "p") and event.ctrl):
            if self._history_search_list is None:
                return True
            mapped = key
            if key == "n" and event.ctrl:
                mapped = "down"
            elif key == "p" and event.ctrl:
                mapped = "up"
            if self._history_search_list.selected_index is None:
                if mapped == "up":
                    return True
                self._history_search_list.set_selected_index(0)
                return True
            mapped_event = input_base.KeyEvent(
                action="down",
                key=mapped,
                ctrl=False,
                alt=False,
                shift=False,
            )
            self._history_search_list.on_key_event(mapped_event)
            return True

        if event.text and not event.ctrl and not event.alt:
            self._history_search_set_query(self._history_search_query + event.text)
            return True

        self._history_search_cancel()
        return False

    def _handle_history_search(self, event: input_base.KeyEvent) -> bool:
        if event.action != "down":
            return True
        top = self._action_stack[-1]
        if top.kind is ActionKind.HISTORY_SEARCH:
            self._history_search_cancel()
            return True

        if top.kind is ActionKind.AUTOCOMPLETE:
            self._pop_action(ActionKind.AUTOCOMPLETE)

        query_view = tui_rich_text_component.RichTextComponent(
            "",
            id="history_search_query",
            markup=False,
            component_style=tui_styles.TOOLBAR_COMPONENT_STYLE,
        )
        select = tui_select_list.SelectListComponent(
            id="history_search",
            allow_no_selection=True,
        )

        def _on_select(item: tui_select_list.SelectItem | None) -> None:
            if item is None:
                self._history_search_cancel()
                return
            if item.value is None:
                self._history_search_cancel()
                return
            self._history_search_accept(item.value)

        select.subscribe_select(_on_select)
        select.set_selected_index(None)
        composite = tui_composite_component.CompositeComponent(
            id="history_search_container",
            component_style=tui_styles.TOOLBAR_COMPONENT_STYLE,
        )
        composite.add_child(query_view)
        composite.add_child(select)

        self._history_search_list = select
        self._history_search_query_view = query_view
        self._push_action(ActionKind.HISTORY_SEARCH, composite)
        self._history_search_set_query("")
        return True

    def _handle_select_list_key_event(
        self,
        component: tui_select_list.SelectListComponent,
        event: input_base.KeyEvent,
        *,
        allow_history_fallback: bool,
    ) -> bool:
        if event.action != "down":
            return True
        key = event.key
        is_ctrl_nav = key in ("n", "p") and event.ctrl
        mapped_nav_key: typing.Optional[str] = None
        if is_ctrl_nav:
            mapped_nav_key = "down" if key == "n" else "up"
        if (
            key in ("up", "down", "tab", "enter", "esc", "escape")
            or mapped_nav_key is not None
        ):
            selected_index = component.selected_index
            if key in ("esc", "escape"):
                mapped_event = input_base.KeyEvent(
                    action="down",
                    key=key,
                    ctrl=False,
                    alt=False,
                    shift=False,
                )
                component.on_key_event(mapped_event)
                return True
            if key == "tab" and not event.ctrl and not event.alt:
                items = component.items
                if not items:
                    return True
                if selected_index is None:
                    if len(items) == 1:
                        component.set_selected_index(0)
                        component.select_current()
                        return True
                    component.set_selected_index(0)
                    return True
                component.select_current()
                return True
            nav_key = mapped_nav_key or key
            if nav_key in ("up", "down"):
                items = component.items
                if not items:
                    return True

                if selected_index is None:
                    if nav_key == "up" and allow_history_fallback:
                        return self._handle_history_up(event)
                    component.set_selected_index(0)
                    return True

                if (
                    allow_history_fallback
                    and nav_key == "down"
                    and selected_index >= len(items) - 1
                ):
                    component.set_selected_index(None)
                    handled, _ = self._maybe_history_down()
                    if handled:
                        return True
                    return True

                mapped_event = input_base.KeyEvent(
                    action="down",
                    key=nav_key,
                    ctrl=False,
                    alt=False,
                    shift=False,
                )
                component.on_key_event(mapped_event)
                return True
            if key == "enter":
                if selected_index is None:
                    self._pop_action(ActionKind.AUTOCOMPLETE)
                    return False
                mapped_event = input_base.KeyEvent(
                    action="down",
                    key="enter",
                    ctrl=False,
                    alt=False,
                    shift=False,
                )
                component.on_key_event(mapped_event)
                return True
        return False

    def _handle_open_command_manager(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if event.action != "down":
            return True

        top = self._action_stack[-1]
        if top.kind is ActionKind.COMMAND_MANAGER:
            self._pop_action(ActionKind.COMMAND_MANAGER)
            return True

        hotkeys = self._build_command_manager_hotkeys()
        component = command_manager_help_component.CommandManagerHelpComponent(
            hotkeys,
            id="command_manager",
        )
        self._push_action(ActionKind.COMMAND_MANAGER, component)
        return True

    def _build_command_manager_hotkeys(self) -> list[tui_command_manager.Hotkey]:
        return [
            tui_command_manager.Hotkey(
                name="Expand last messages",
                category="Messages",
                mapping=tui_input_component.KeyBinding("e"),
                handler=self._handle_expand_last_components,
            ),
            tui_command_manager.Hotkey(
                name="Collapse last messages",
                category="Messages",
                mapping=tui_input_component.KeyBinding("c"),
                handler=self._handle_collapse_last_components,
            ),
            tui_command_manager.Hotkey(
                name="Expand last tool steps",
                category="Tools",
                mapping=tui_input_component.KeyBinding("e", shift=True),
                handler=self._handle_expand_last_tool_steps,
            ),
            tui_command_manager.Hotkey(
                name="Collapse last tool steps",
                category="Tools",
                mapping=tui_input_component.KeyBinding("c", shift=True),
                handler=self._handle_collapse_last_tool_steps,
            ),
            tui_command_manager.Hotkey(
                name="Open logs",
                category="Navigation",
                mapping=tui_input_component.KeyBinding("l"),
                handler=self._handle_open_logs,
            ),
        ]

    def _handle_history_up(self, event: input_base.KeyEvent) -> bool:
        component = self._input_component
        if component.cursor_row != 0:
            return False
        self._history_manager.update_current(component.text)
        new_text = self._history_manager.navigate_previous(component.text)
        if new_text is None:
            return False
        self._suppress_history_update += 1
        try:
            component.text = new_text
            lines = component.lines
            if lines:
                last_row = len(lines) - 1
                last_col = len(lines[last_row])
                component.set_cursor_position(last_row, last_col)
        finally:
            self._suppress_history_update -= 1
        return True

    def _maybe_history_down(self) -> tuple[bool, bool]:
        component = self._input_component
        lines = component.lines
        if not lines:
            return False, False
        last_row = len(lines) - 1
        if component.cursor_row != last_row:
            return False, False
        self._history_manager.update_current(component.text)
        new_text = self._history_manager.navigate_next()
        if new_text is None:
            return False, True
        self._suppress_history_update += 1
        try:
            component.text = new_text
            lines = component.lines
            if lines:
                component.set_cursor_position(0, 0)
        finally:
            self._suppress_history_update -= 1
        return True, False

    def _handle_history_down(self, event: input_base.KeyEvent) -> bool:
        _ = event
        handled, _ = self._maybe_history_down()
        return handled

    def _handle_stop(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if self._on_stop is None:
            return False
        asyncio.create_task(self._on_stop())
        return True

    def _apply_progressive_collapse(
        self,
        *,
        collapsed: bool,
        include_tools: bool,
        include_non_tools: bool,
    ) -> bool:
        terminal = self._terminal
        components = terminal.components
        if len(components) <= 3:
            return False

        message_components = components[1:-2]
        if not message_components:
            return False

        filtered: list[tui_terminal.Component] = []
        for component in message_components:
            is_tool = isinstance(
                component,
                tool_call_req_component.ToolCallReqComponent,
            )
            if is_tool and not include_tools:
                continue
            if (not is_tool) and not include_non_tools:
                continue
            if not component.supports_collapse:
                continue
            filtered.append(component)

        if not filtered:
            return False

        count = self._progressive_count
        if count < 1:
            count = 1
        take = 10 * count
        candidates = filtered[-take:]
        if not candidates:
            return False

        for component in candidates:
            component.set_collapsed(collapsed)

        return True

    def _handle_collapse_last_components(self, event: input_base.KeyEvent) -> bool:
        _ = event
        self._apply_progressive_collapse(
            collapsed=True,
            include_tools=False,
            include_non_tools=True,
        )
        return True

    def _handle_expand_last_components(self, event: input_base.KeyEvent) -> bool:
        _ = event
        self._apply_progressive_collapse(
            collapsed=False,
            include_tools=False,
            include_non_tools=True,
        )
        return True

    def _handle_collapse_last_tool_steps(self, event: input_base.KeyEvent) -> bool:
        _ = event
        self._apply_progressive_collapse(
            collapsed=True,
            include_tools=True,
            include_non_tools=False,
        )
        return True

    def _handle_expand_last_tool_steps(self, event: input_base.KeyEvent) -> bool:
        _ = event
        self._apply_progressive_collapse(
            collapsed=False,
            include_tools=True,
            include_non_tools=False,
        )
        return True

    def _handle_eof(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if self._on_eof is None:
            return False
        asyncio.create_task(self._on_eof())
        return True

    def _handle_open_logs(self, event: input_base.KeyEvent) -> bool:
        _ = event
        if self._on_open_logs is None:
            return False
        asyncio.create_task(self._on_open_logs())
        return True

    def _handle_input_event(self, event: input_base.InputEvent) -> None:
        if isinstance(event, input_base.PasteEvent):
            text = event.text
            if text:
                self._input_component.paste_text(text)
            return

        if isinstance(event, input_base.KeyEvent):
            binding = tui_input_component.KeyBinding(
                key=event.key,
                ctrl=event.ctrl,
                alt=event.alt,
                shift=event.shift,
            )
            if event.action == "down" and binding in self._progressive_keybindings:
                if self._progressive_hotkey == binding:
                    self._progressive_count += 1
                else:
                    self._progressive_hotkey = binding
                    self._progressive_count = 1
            elif event.action == "down":
                if binding.key in ("esc", "escape"):
                    pass
                elif binding.key == "space" and binding.ctrl:
                    pass
                else:
                    self._progressive_hotkey = None
                    self._progressive_count = 0

            terminal = self._terminal
            if not terminal.has_screens:
                handled = self._handle_input_key_event(event)
                if handled:
                    return

        self._terminal._handle_input_event(event)

    async def start(self) -> None:
        await self._terminal.start()
        if self._input_handler is not None and self._input_task is None:
            loop = asyncio.get_running_loop()
            self._input_task = loop.create_task(self._input_handler.run())
        self._terminal.console.control(rich_control.Control.show_cursor(False))
        await self._terminal.render()

    async def stop(self) -> None:
        task = self._input_task
        self._input_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._terminal.stop()
        self._terminal.console.control(rich_control.Control.show_cursor(True))

    def add_markdown(
        self,
        markdown: str,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        collapse_lines: int = 10
        collapsed: bool | None = None
        if display is not None:
            if display.collapse_lines is not None:
                collapse_lines = display.collapse_lines
            collapsed = display.collapse
        component = tui_markdown_component.MarkdownComponent(
            markdown,
            compact_lines=collapse_lines,
            collapsed=collapsed,
            component_style=component_style,
            render_mode=self._markdown_render_mode,
        )
        self._terminal.insert_component(-2, component)

    def add_rich_text(
        self,
        text: str,
        component_style: tui_terminal.ComponentStyle | None = None,
        markup: bool = True,
    ) -> None:
        component = tui_rich_text_component.RichTextComponent(
            text,
            component_style=component_style,
            markup=markup,
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
        elif text_format == "plain":
            self.add_rich_text(text, component_style=component_style, markup=False)
        else:
            self.add_rich_text(text, component_style=component_style)

    def _format_message_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        return message.text

    def _upsert_step_output_component(
        self,
        step: vocode_state.Step,
        text: str,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        step_id = str(step.id)
        existing = self._step_components.get(step_id)
        if existing is not None:
            existing.markdown_render_mode = self._markdown_render_mode
            existing.set_value(text=text, content_type=step.content_type)
            if display is not None:
                if display.collapse_lines is not None:
                    existing.compact_lines = display.collapse_lines
                if display.collapse is not None:
                    existing.set_collapsed(display.collapse)
            if component_style is not None:
                existing.component_style = component_style
            return

        collapse_lines: int = 10
        collapsed: bool = False
        if display is not None:
            if display.collapse_lines is not None:
                collapse_lines = display.collapse_lines
            collapsed = display.collapse

        component = tui_step_output_component.StepOutputComponent(
            text=text,
            content_type=step.content_type,
            compact_lines=collapse_lines,
            collapsed=collapsed,
            id=step_id,
            component_style=component_style,
            markdown_render_mode=self._markdown_render_mode,
        )
        self._step_components[step_id] = component
        self._step_component_ids.add(step_id)
        self._terminal.insert_component(-2, component)

    def _format_prompt_markdown(self, step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None
        if message.text is None:
            return None
        return message.text

    def _upsert_markdown_component(
        self,
        step: vocode_state.Step,
        markdown: str,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        step_id = str(step.id)
        existing = self._step_components.get(step_id)
        if existing is not None:
            existing.markdown = markdown
            if display is not None:
                if display.collapse_lines is not None:
                    existing.compact_lines = display.collapse_lines
                if display.collapse is not None:
                    existing.set_collapsed(display.collapse)
            if component_style is not None:
                existing.component_style = component_style
            return

        collapse_lines: int = 10
        collapsed: bool = False
        if display is not None:
            if display.collapse_lines is not None:
                collapse_lines = display.collapse_lines
            collapsed = display.collapse

        component = tui_markdown_component.MarkdownComponent(
            markdown,
            compact_lines=collapse_lines,
            collapsed=collapsed,
            id=step_id,
            component_style=component_style,
            render_mode=self._markdown_render_mode,
        )
        self._step_components[step_id] = component
        self._step_component_ids.add(step_id)
        self._terminal.insert_component(-2, component)

    def _handle_output_message_step(
        self,
        step: vocode_state.Step,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
    ) -> None:
        message = step.message
        if message is None:
            return

        self._upsert_step_output_component(
            step,
            message.text,
            display=display,
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

        step_id = str(step.id)
        try:
            existing = typing.cast(
                tui_rich_text_component.RichTextComponent,
                self._terminal.get_component(step_id),
            )
            existing.text = prefixed
            existing.component_style = tui_styles.INPUT_MESSAGE_COMPONENT_STYLE
        except KeyError:
            component = tui_rich_text_component.RichTextComponent(
                prefixed,
                id=step_id,
                component_style=tui_styles.INPUT_MESSAGE_COMPONENT_STYLE,
            )
            self._step_component_ids.add(step_id)
            self._terminal.insert_component(-2, component)

    def _handle_prompt_step(
        self,
        step: vocode_state.Step,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
    ) -> None:
        markdown = self._format_prompt_markdown(step)
        if markdown is None:
            return
        self._upsert_markdown_component(
            step,
            markdown,
            display=display,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_rejection_step(self, step: vocode_state.Step) -> None:
        message = step.message
        text = "User declined."
        if message is not None:
            raw = message.text.strip()
            if raw:
                text = raw
        step_id = str(step.id)
        self._upsert_markdown_component(
            step,
            text,
            component_style=tui_styles.OUTPUT_MESSAGE_STYLE,
        )

    def _handle_tool_request_step(
        self,
        step: vocode_state.Step,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
    ) -> None:
        step_id = str(step.id)
        terminal = self._terminal
        try:
            component = typing.cast(
                tool_call_req_component.ToolCallReqComponent,
                terminal.get_component(step_id),
            )
            component.set_step(step)
        except KeyError:
            component = tool_call_req_component.ToolCallReqComponent(
                step=step,
                component_style=tui_styles.TOOL_CALL_COMPONENT_STYLE,
            )
            self._step_component_ids.add(step_id)
            message = step.message
            if message is not None:
                disable_stats = False
                expand_for_confirmation = False
                tui_settings = terminal.settings.tui
                for tool_call in message.tool_call_requests:
                    name = tool_call.name
                    manager = tui_tcf.ToolCallFormatterManager.instance()
                    if not manager.show_execution_stats(name):
                        disable_stats = True
                        break
                    status = tool_call.status
                    if (
                        status is vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION
                        and tui_settings.expand_confirm_tools
                    ):
                        expand_for_confirmation = True
                if disable_stats:
                    component.set_show_execution_stats(False)
                if expand_for_confirmation:
                    component.set_collapsed(False)

            if display is not None and display.tool_collapse is not None:
                component.set_collapsed(display.tool_collapse)

            terminal.insert_component(-2, component)

    def _handle_default_step(self, step: vocode_state.Step) -> None:
        markdown = self._format_message_markdown(step)
        if markdown is None:
            return
        self._upsert_markdown_component(step, markdown)

    def handle_step_deleted(self, step_ids: list[str]) -> None:
        terminal = self._terminal
        with terminal.suspend_auto_render():
            for step_id in step_ids:
                if step_id in self._step_components:
                    component = self._step_components.pop(step_id)
                    terminal.remove_component(component)
                if step_id in self._step_component_ids:
                    self._step_component_ids.remove(step_id)
                    try:
                        component = terminal.get_component(step_id)
                        terminal.remove_component(component)
                    except KeyError:
                        pass

    def handle_step(
        self,
        step: vocode_state.Step,
        display: manager_proto.RunnerReqDisplayOpts | None = None,
    ) -> None:
        if display is not None and display.visible is False:
            return
        if step.type is vocode_state.StepType.APPROVAL:
            return
        mode = step.output_mode
        if mode == vocode_models.OutputMode.HIDE_ALL:
            if step.message is not None:
                return
        elif mode == vocode_models.OutputMode.HIDE_FINAL:
            if step.is_final and step.message is not None:
                return
        if step.type == vocode_state.StepType.OUTPUT_MESSAGE:
            self._handle_output_message_step(step, display=display)
            return
        if step.type in (
            vocode_state.StepType.PROMPT,
            vocode_state.StepType.PROMPT_CONFIRM,
        ):
            self._handle_prompt_step(step, display=display)
            return
        handler = self._step_handlers.get(step.type)
        if handler is not None:
            if step.type is vocode_state.StepType.TOOL_REQUEST:
                self._handle_tool_request_step(step, display=display)
            else:
                handler(step)
            return
        self._handle_default_step(step)

    def handle_ui_state(self, packet: manager_proto.UIServerStatePacket) -> None:
        self._ui_state = packet
        self._update_toolbar_from_ui_state()

    def handle_progress(self, packet: manager_proto.ProgressPacket) -> None:
        pid = packet.progress_id
        if pid is None:
            return
        status = packet.status
        if status is manager_proto.ProgressStatus.START:
            self._progress_by_id[pid] = packet
            self._active_progress_id = pid
            gate = self._progress_gate_tasks.pop(pid, None)
            if gate is not None and not gate.done():
                gate.cancel()

            async def _gate() -> None:
                try:
                    await asyncio.sleep(PROGRESS_VISIBILITY_DELAY_S)
                except asyncio.CancelledError:
                    return
                if pid not in self._progress_by_id:
                    return
                self._progress_visible_by_id.add(pid)
                self._update_progress_component()

            loop = asyncio.get_running_loop()
            self._progress_gate_tasks[pid] = loop.create_task(_gate())
            return

        if status is manager_proto.ProgressStatus.UPDATE:
            self._progress_by_id[pid] = packet
            if packet.done is True:
                self._on_progress_completed(pid, packet)
                return
            if pid in self._progress_visible_by_id and pid == self._active_progress_id:
                self._update_progress_component()
            return

        if status is manager_proto.ProgressStatus.END:
            self._on_progress_completed(pid, packet)
            return

    def _on_progress_completed(
        self,
        pid: str,
        packet: manager_proto.ProgressPacket | None = None,
    ) -> None:
        stored = self._progress_by_id.get(pid)
        on_complete = None
        complete_message = None
        if packet is not None:
            on_complete = packet.on_complete
            complete_message = packet.complete_message
        if on_complete is None and stored is not None:
            on_complete = stored.on_complete
        if complete_message is None and stored is not None:
            complete_message = stored.complete_message

        self._progress_by_id.pop(pid, None)
        self._progress_visible_by_id.discard(pid)
        gate = self._progress_gate_tasks.pop(pid, None)
        if gate is not None and not gate.done():
            gate.cancel()
        if self._active_progress_id == pid:
            self._active_progress_id = None
            if self._progress_visible_by_id:
                self._active_progress_id = next(iter(self._progress_visible_by_id))
        self._update_progress_component()

        if on_complete is manager_proto.ProgressOnComplete.MESSAGE:
            text = complete_message
            if text is None or not text.strip():
                text = "Completed"
            self.add_text_message(text, text_format="plain")

    def _update_progress_component(self) -> None:
        terminal = self._terminal
        pid = self._active_progress_id
        visible = pid is not None and pid in self._progress_visible_by_id
        packet = self._progress_by_id.get(pid) if visible and pid is not None else None

        component = self._progress_component
        if packet is None:
            if component is None:
                return
            terminal.remove_component(component)
            self._progress_component = None
            return

        if component is None:
            component = progress_component.ProgressComponent(
                id="progress",
                component_style=tui_styles.TOOLBAR_COMPONENT_STYLE,
            )
            self._progress_component = component
            terminal.insert_component(-2, component)

        component.set_progress(packet)

    def set_input_panel_title(
        self,
        title: str | None,
        subtitle: str | None = None,
    ) -> None:
        style = self._input_component.component_style
        if style is None:
            style = tui_styles.INPUT_COMPONENT_STYLE
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

    def _capture_autocomplete_context(self, row: int, col: int) -> bool:
        lines = self._input_component.lines
        if row < 0 or row >= len(lines):
            return False
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
        return True

    def _schedule_autocomplete_request(self) -> None:
        if self._on_autocomplete_request is None:
            return
        if (
            self._last_autocomplete_text is None
            or self._last_autocomplete_row is None
            or self._last_autocomplete_col is None
        ):
            return

        self._autocomplete_pending = True
        request_task = self._autocomplete_task
        if request_task is not None and not request_task.done():
            return

        loop = asyncio.get_running_loop()

        async def _throttled() -> None:
            while True:
                if not self._autocomplete_pending:
                    return
                self._autocomplete_pending = False
                current_text = self._last_autocomplete_text
                current_row = self._last_autocomplete_row
                current_col = self._last_autocomplete_col
                if current_text is None or current_row is None or current_col is None:
                    return
                await self._on_autocomplete_request(
                    current_text,
                    current_row,
                    current_col,
                )
                try:
                    await asyncio.sleep(AUTOCOMPLETE_DEBOUNCE_MS / 1000.0)
                except asyncio.CancelledError:
                    return

        self._autocomplete_task = loop.create_task(_throttled())

    def _handle_cursor_event(self, row: int, col: int) -> None:
        if self._action_stack[-1].kind is ActionKind.HISTORY_SEARCH:
            return
        if self._on_autocomplete_request is None:
            return
        ok = self._capture_autocomplete_context(row, col)
        if not ok:
            return
        self._schedule_autocomplete_request()

    def _handle_change(self, value: str) -> None:
        if self._suppress_history_update <= 0:
            self._history_manager.update_current(value)
        if self._action_stack[-1].kind is ActionKind.HISTORY_SEARCH:
            return
        if self._on_autocomplete_request is None:
            return
        row = self._input_component.cursor_row
        col = self._input_component.cursor_col
        ok = self._capture_autocomplete_context(row, col)
        if not ok:
            return
        self._schedule_autocomplete_request()

    def handle_autocomplete_options(
        self,
        items: list[manager_proto.AutocompleteItem] | None,
    ) -> None:
        if self._action_stack[-1].kind is ActionKind.HISTORY_SEARCH:
            return
        self._autocomplete_items = items
        if not items:
            self._pop_action(ActionKind.AUTOCOMPLETE)
            return
        top = self._action_stack[-1]
        if top.kind is ActionKind.AUTOCOMPLETE:
            component = typing.cast(tui_select_list.SelectListComponent, top.component)
            component.set_items(
                [
                    {
                        "id": str(index),
                        "text": item.title,
                        "value": str(index),
                    }
                    for index, item in enumerate(items)
                ]
            )
            return
        select = tui_select_list.SelectListComponent(
            id="autocomplete",
            allow_no_selection=True,
        )

        def _on_select(item: tui_select_list.SelectItem | None) -> None:
            if item is None:
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return

            items = self._autocomplete_items
            if not items:
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return

            if item.value is None:
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            try:
                selected_index = int(item.value)
            except ValueError:
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            if selected_index < 0 or selected_index >= len(items):
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            selected = items[selected_index]

            lines = self._input_component.lines
            row = self._input_component.cursor_row
            if row < 0 or row >= len(lines):
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            line = lines[row]

            start = selected.replace_start
            if start < 0 or start > len(line):
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            end = start + len(selected.replace_text)
            if end > len(line):
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return
            if line[start:end] != selected.replace_text:
                self._pop_action(ActionKind.AUTOCOMPLETE)
                return

            new_line = line[:start] + selected.insert_text + line[end:]
            all_lines = list(lines)
            all_lines[row] = new_line
            self._input_component.text = "\n".join(all_lines)
            self._input_component.set_cursor_position(
                row, start + len(selected.insert_text)
            )
            self._pop_action(ActionKind.AUTOCOMPLETE)

        select.subscribe_select(_on_select)
        select.set_items(
            [
                {
                    "id": str(index),
                    "text": item.title,
                    "value": str(index),
                }
                for index, item in enumerate(items)
            ]
        )
        select.set_selected_index(None)
        self._push_action(ActionKind.AUTOCOMPLETE, select)

    def _push_action(self, kind: ActionKind, component: tui_terminal.Component) -> None:
        current_item = self._action_stack[-1]
        current_component = current_item.component
        terminal = self._terminal

        if terminal is not None:
            if current_component in terminal._animation_components:
                current_item.animated = True

        if component is not current_component:
            if terminal is not None:
                terminal.remove_component(current_component)
                terminal.append_component(component)
        self._action_stack.append(ActionItem(kind=kind, component=component))
        self._toolbar_component = component

    def _pop_action(self, kind: ActionKind | None = None) -> None:
        if len(self._action_stack) <= 1:
            return
        top = self._action_stack[-1]
        if kind is not None and top.kind is not kind:
            return
        terminal = self._terminal
        if terminal is not None:
            terminal.remove_component(top.component)
        self._action_stack.pop()

        new_top = self._action_stack[-1]
        self._toolbar_component = new_top.component

        if terminal is not None:
            # If the component was removed but not yet purged (deferred removal),
            # we need to ensure it's properly re-attached.
            if self._toolbar_component.terminal is None:
                if self._toolbar_component in terminal.components:
                    terminal.components.remove(self._toolbar_component)
                    if hasattr(terminal, "_removed_components"):
                        if self._toolbar_component in terminal._removed_components:
                            terminal._removed_components.remove(self._toolbar_component)

            if self._toolbar_component not in terminal.components:
                terminal.append_component(self._toolbar_component)

            if new_top.animated:
                if isinstance(
                    self._toolbar_component,
                    toolbar_component.ToolbarComponent,
                ):
                    toolbar = typing.cast(
                        toolbar_component.ToolbarComponent,
                        self._toolbar_component,
                    )
                    toolbar.restore_animation()
                else:
                    terminal.register_animation(self._toolbar_component)

    def _update_toolbar_from_ui_state(self) -> None:
        toolbar = self._base_toolbar_component
        toolbar.set_state(self._ui_state)
