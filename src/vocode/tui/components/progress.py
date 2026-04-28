from __future__ import annotations

from typing import Final, Optional
import time

from rich import console as rich_console
from rich import progress as rich_progress
from rich import text as rich_text

from vocode.manager import proto as manager_proto
from vocode.tui import lib as tui_terminal
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import unicode as tui_unicode
from vocode.tui.lib.components import renderable as renderable_component


MIN_RENDER_INTERVAL_S: Final[float] = 0.1


class ProgressComponent(renderable_component.RenderableComponentBase):
    def __init__(
        self,
        id: str | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._progress: manager_proto.ProgressPacket | None = None
        self._frame_index = 0
        self._animated = False
        self._last_rendered_at: float | None = None

    def set_progress(self, packet: manager_proto.ProgressPacket | None) -> None:
        self._progress = packet
        self._mark_dirty()
        self._update_animation(force=True)

    def _update_animation(self, force: bool = False) -> None:
        terminal = self.terminal
        if terminal is None:
            return
        should_animate = False
        packet = self._progress
        if (
            packet is not None
            and packet.mode is manager_proto.ProgressMode.INDETERMINATE
        ):
            should_animate = True
        if not force and should_animate == self._animated:
            return
        self._animated = should_animate
        if should_animate:
            terminal.register_animation(self)
        else:
            terminal.deregister_animation(self)

    def _get_spinner_prefix(self) -> str:
        terminal = self.terminal
        if terminal is None:
            return ""
        unicode_manager = terminal.unicode
        frame = unicode_manager.spinner_frame(
            self._frame_index,
            tui_unicode.SpinnerVariant.DOTS,
        )
        frames = unicode_manager.spinner_frames(tui_unicode.SpinnerVariant.DOTS)
        self._frame_index = (self._frame_index + 1) % len(frames)
        return frame.strip()

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        packet = self._progress
        if packet is None:
            return rich_text.Text("")

        title = packet.title or "Working"
        desc = title
        if packet.message:
            desc = f"{title}: {packet.message}"

        now = time.monotonic()
        last = self._last_rendered_at
        if last is not None and (now - last) < MIN_RENDER_INTERVAL_S:
            self._frame_index = 0
        self._last_rendered_at = now

        if (
            packet.mode is manager_proto.ProgressMode.DETERMINISTIC
            and packet.total is not None
        ):
            progress = rich_progress.Progress(
                rich_progress.TextColumn("{task.description}"),
                rich_progress.BarColumn(bar_width=None),
                rich_progress.TaskProgressColumn(),
                rich_progress.TextColumn(
                    "{task.completed:.0f}/{task.total:.0f} {task.fields[unit]}",
                    markup=False,
                ),
                auto_refresh=False,
                expand=True,
            )
            progress.add_task(
                desc,
                total=float(packet.total),
                completed=int(packet.completed or 0),
                unit=packet.unit or "",
            )
            return progress

        if packet.bar_type is manager_proto.ProgressBarType.SPINNER:
            spinner = self._get_spinner_prefix()
            if spinner:
                desc = f"{spinner} {desc}"
            progress = rich_progress.Progress(
                rich_progress.TextColumn("{task.description}"),
                auto_refresh=False,
                expand=True,
            )
            progress.add_task(desc, total=None)
            return progress

        progress = rich_progress.Progress(
            rich_progress.TextColumn("{task.description}"),
            rich_progress.BarColumn(bar_width=None),
            auto_refresh=False,
            expand=True,
        )
        progress.add_task(desc, total=None)
        return progress
