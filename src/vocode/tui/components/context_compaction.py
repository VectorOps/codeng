from __future__ import annotations

from rich import console as rich_console
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.runner.executors.llm.compaction import CompactionSummaryState
from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import renderable as tui_renderable_component


class ContextCompactionComponent(tui_renderable_component.RenderableComponentBase):
    def __init__(
        self,
        step: vocode_state.Step,
        summary_state: CompactionSummaryState | None,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(id=id, component_style=component_style)
        self._step = step
        self._summary_state = summary_state

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        _ = console
        summary_state = self._summary_state
        if summary_state is None:
            return rich_text.Text("Context compacted.", style="dim")

        summarized_count = len(summary_state.compacted_step_ids)
        text = rich_text.Text(style="dim")
        text.append("Compaction complete", style="cyan")
        text.append("  ")
        text.append(f"{summarized_count} step")
        if summarized_count != 1:
            text.append("s")
        text.append("  ")
        text.append(f"~{summary_state.tokens_before}", style="yellow")
        if summary_state.tokens_after_estimate is not None:
            text.append(" -> ")
            text.append(
                f"~{summary_state.tokens_after_estimate}",
                style="green",
            )
        text.append(" tokens")
        return text
