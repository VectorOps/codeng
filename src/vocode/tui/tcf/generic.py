from __future__ import annotations

import typing
import pydantic

from vocode.logger import logger
from vocode import state as vocode_state
from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.tcf import render_utils as tcf_render_utils


class _GenericToolCallFormatterOptions(pydantic.BaseModel):
    max_pairs: int = 6
    max_value_chars: int = 80
    min_value_chars: int = 8
    format_output: bool = False
    max_output_chars: int = 200


@tui_tcf.ToolCallFormatterManager.register("generic")
class GenericToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    def _parse_options(
        self, config: vocode_settings.ToolCallFormatter | None
    ) -> tuple[_GenericToolCallFormatterOptions, str | None]:
        if config is None:
            return _GenericToolCallFormatterOptions(), None
        try:
            return (
                _GenericToolCallFormatterOptions.model_validate(config.options or {}),
                None,
            )
        except pydantic.ValidationError as e:
            msg = tcf_render_utils.to_single_line(str(e))
            msg, _ = tcf_render_utils.truncate_to_width(msg, 120)
            return _GenericToolCallFormatterOptions(), msg

    def render(
        self,
        terminal: tui_terminal.Terminal,
        req: typing.Optional[vocode_state.ToolCallReq],
        resp: typing.Optional[vocode_state.ToolCallResp],
        context: tui_tcf.ToolCallRenderContext,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        tool_name = ""
        arguments: typing.Any = None
        if req is not None:
            tool_name = req.name
            arguments = req.arguments
        elif resp is not None:
            tool_name = resp.name
        else:
            return None

        display_name = self.format_tool_name(tool_name)
        if config is not None and config.title:
            display_name = config.title
        options, options_error = self._parse_options(config)
        max_pairs = options.max_pairs
        max_value_chars = options.max_value_chars
        min_value_chars = options.min_value_chars

        raw_pairs: list[tuple[str, typing.Any]] = []
        if isinstance(arguments, dict):
            for k, v in arguments.items():
                raw_pairs.append((str(k), v))
        elif arguments is not None:
            raw_pairs.append(("args", arguments))

        if max_pairs > 0:
            raw_pairs = raw_pairs[:max_pairs]

        rendered_pairs: list[tuple[str, str]] = []
        for k, v in raw_pairs:
            rendered_v = tcf_render_utils.to_single_line(
                tcf_render_utils.stringify_value(v)
            )
            if tcf_render_utils.cell_len(rendered_v) > max_value_chars:
                rendered_v, _ = tcf_render_utils.truncate_to_width(
                    rendered_v,
                    max_value_chars,
                )
            rendered_pairs.append((k, rendered_v))

        joiner = ", "
        suffix_text = ""
        if options_error is not None:
            suffix_text = (
                f" [invalid formatter options; using defaults: {options_error}]"
            )
        status_suffix = tcf_render_utils.build_status_suffix(context)
        if status_suffix:
            suffix_text = f"{suffix_text} [{status_suffix}]"
        fitted_pairs, need_ellipsis = tcf_render_utils.fit_kv_pairs_to_width(
            max_width=context.max_width,
            prefix_len=tcf_render_utils.cell_len(
                context.status_icon or terminal.unicode.glyph(":circle:")
            )
            + 1,
            display_name_len=tcf_render_utils.cell_len(display_name),
            suffix_len=tcf_render_utils.cell_len(suffix_text),
            pairs=rendered_pairs,
            min_value_chars=min_value_chars,
            joiner=joiner,
        )
        if need_ellipsis:
            fitted_pairs.append(("...", ""))

        output_text = None
        if resp is not None and config is not None:
            if config.show_output or options.format_output:
                output_text = tcf_render_utils.to_single_line(
                    tcf_render_utils.stringify_value(resp.result)
                )
                if len(output_text) > options.max_output_chars:
                    output_text = output_text[: options.max_output_chars - 3] + "..."

        pairs: list[tuple[str, str]] = []
        for key, value in fitted_pairs:
            if key == "...":
                continue
            pairs.append((key, value))
        line = tcf_render_utils.build_tool_line(
            terminal,
            display_name,
            pairs=pairs,
            context=context,
            prefix_icon=context.status_icon,
            output_text=output_text,
        )
        if need_ellipsis:
            line.append(", ...", style=tui_styles.TOOL_CALL_META_STYLE)
        if options_error is not None:
            line.append(" ", style=tui_styles.TOOL_CALL_META_STYLE)
            line.append(
                f"[invalid formatter options; using defaults: {options_error}]",
                style=tui_styles.TOOL_CALL_META_STYLE,
            )
        if context.max_width > 0:
            line.truncate(context.max_width, overflow="ellipsis")
        return line
