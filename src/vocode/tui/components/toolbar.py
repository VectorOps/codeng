from __future__ import annotations

from typing import Final
import datetime
from rich import console as rich_console
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.logger import logger
from vocode.manager import proto as manager_proto
from vocode.lib import formatting as lib_formatting
from vocode.tui import lib as tui_terminal
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import unicode as tui_unicode
from vocode.tui.lib.components import renderable as renderable_component


RUNNER_STATUS_LABELS: Final[dict[vocode_state.RunnerStatus, str]] = {
    vocode_state.RunnerStatus.IDLE: "idle",
    vocode_state.RunnerStatus.RUNNING: "running",
    vocode_state.RunnerStatus.WAITING_INPUT: "waiting for input",
    vocode_state.RunnerStatus.STOPPED: "canceled",
    vocode_state.RunnerStatus.FINISHED: "finished",
}


class ToolbarComponent(renderable_component.RenderableComponentBase):
    def __init__(
        self,
        id: str | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._ui_state: manager_proto.UIServerStatePacket | None = None
        self._workflow_label = ""
        self._status: vocode_state.RunnerStatus | None = None
        self._frame_index = 0
        self._animated = False
        self._active_workflow_llm_usage: vocode_state.LLMUsageStats | None = None
        self._last_step_llm_usage: vocode_state.LLMUsageStats | None = None
        self._project_llm_usage: vocode_state.LLMUsageStats | None = None
        self._active_node_started_at: datetime.datetime | None = None

    @property
    def text(self) -> str:
        return self._workflow_label

    def set_state(
        self,
        ui_state: manager_proto.UIServerStatePacket | None,
    ) -> None:
        self._ui_state = ui_state
        self._update_from_state()
        self._mark_dirty()

    def _update_from_state(self) -> None:
        ui_state = self._ui_state
        workflow_label = ""
        status: vocode_state.RunnerStatus | None = None
        active_usage: vocode_state.LLMUsageStats | None = None
        last_step_usage: vocode_state.LLMUsageStats | None = None
        project_usage: vocode_state.LLMUsageStats | None = None
        active_node_started_at: datetime.datetime | None = None
        if ui_state is not None:
            if ui_state.runners:
                labels: list[str] = []
                for frame in ui_state.runners:
                    workflow_name = frame.workflow_name
                    node_name = frame.node_name
                    if node_name:
                        label = f"{workflow_name}@{node_name}"
                    else:
                        label = workflow_name
                    labels.append(label)
                workflow_label = " > ".join(labels)
                status = ui_state.runners[-1].status
            active_node_started_at = ui_state.active_node_started_at
            active_usage = ui_state.active_workflow_llm_usage
            last_step_usage = ui_state.last_step_llm_usage
            project_usage = ui_state.project_llm_usage
        self._workflow_label = workflow_label
        self._status = status
        self._active_workflow_llm_usage = active_usage
        self._last_step_llm_usage = last_step_usage
        self._project_llm_usage = project_usage
        self._active_node_started_at = active_node_started_at
        self._update_animation()

    def _get_status_label(self) -> str:
        status = self._status
        if status is None:
            return ""

        label = RUNNER_STATUS_LABELS.get(status)
        if label is not None:
            return label
        return status

    @staticmethod
    def _format_elapsed(started_at: datetime.datetime | None) -> str:
        if started_at is None:
            return ""
        now = datetime.datetime.now(datetime.timezone.utc)
        if started_at.tzinfo is None:
            started = started_at.replace(tzinfo=datetime.timezone.utc)
        else:
            started = started_at
        if now <= started:
            return "0s"
        delta = now - started
        total_seconds = int(delta.total_seconds())
        if total_seconds < 1:
            return "<1s"
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _update_animation(self, force: bool = False) -> None:
        terminal = self.terminal
        if terminal is None:
            return
        should_animate = self._status is vocode_state.RunnerStatus.RUNNING
        if not force and should_animate == self._animated:
            return
        self._animated = should_animate
        if should_animate:
            terminal.register_animation(self)
        else:
            terminal.deregister_animation(self)

    def restore_animation(self) -> None:
        self._update_animation(force=True)

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        status = self._status
        label = self._workflow_label

        frame_text = ""
        if status is vocode_state.RunnerStatus.RUNNING:
            terminal = self.terminal
            if terminal is not None:
                uni = terminal.unicode
                frame = uni.spinner_frame(
                    self._frame_index,
                    tui_unicode.SpinnerVariant.BRAILLE,
                )
                frames = uni.spinner_frames(tui_unicode.SpinnerVariant.BRAILLE)
                self._frame_index = (self._frame_index + 1) % len(frames)
                frame_text = frame.strip()
        else:
            self._frame_index = 0

        status_text = self._get_status_label()

        elapsed_text = ""
        if status is vocode_state.RunnerStatus.RUNNING:
            elapsed_text = self._format_elapsed(self._active_node_started_at)

        bracket_parts: list[str] = []
        if status_text:
            bracket_parts.append(status_text)
        if frame_text:
            bracket_parts.append(frame_text)
        if elapsed_text:
            bracket_parts.append(elapsed_text)

        suffix = ""
        if bracket_parts:
            suffix = f"[{' '.join(bracket_parts)}]"

        parts: list[str] = []
        if label:
            parts.append(label)
        if suffix:
            parts.append(suffix)

        main_text = " ".join(parts)

        last_step_usage = self._last_step_llm_usage
        project_usage = self._project_llm_usage

        step_input_tokens = 0
        step_input_limit = 0
        if last_step_usage is not None:
            step_input_tokens = int(last_step_usage.prompt_tokens or 0)
            if last_step_usage.input_token_limit is not None:
                step_input_limit = int(last_step_usage.input_token_limit)

        total_sent = 0
        total_received = 0
        total_cost = 0.0
        if project_usage is not None:
            total_sent = int(project_usage.prompt_tokens or 0)
            total_received = int(project_usage.completion_tokens or 0)
            total_cost = float(project_usage.cost_dollars or 0.0)

        usage_parts: list[str] = []
        step_input_tokens_str = lib_formatting.format_int_compact(step_input_tokens)
        step_input_limit_str = lib_formatting.format_int_compact(step_input_limit)

        percentage = 0
        if step_input_limit > 0:
            percentage = int((step_input_tokens / step_input_limit) * 100)

        usage_parts.append(
            f"{step_input_tokens_str}/{step_input_limit_str} ({percentage}%)"
        )
        sent_str = lib_formatting.format_int_compact(total_sent)
        received_str = lib_formatting.format_int_compact(total_received)
        cost_str = lib_formatting.format_cost_compact(total_cost)
        usage_parts.append(f"ts: {sent_str} tr: {received_str} ${cost_str}")
        usage_text = " | ".join(usage_parts)

        if not usage_text:
            return rich_text.Text(main_text)

        if not main_text:
            return rich_text.Text(usage_text)

        width = console.width
        left = main_text
        right = usage_text
        min_space = 1
        if width <= len(left) + min_space + len(right):
            full_text = f"{left} {right}"
        else:
            spaces = width - len(left) - len(right)
            if spaces < min_space:
                spaces = min_space
            full_text = f"{left}{' ' * spaces}{right}"

        return rich_text.Text(full_text)
