from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import typing

from rich import console as rich_console
from rich import align as rich_align
from rich import box as rich_box
from rich import segment as rich_segment
from rich import style as rich_style
from rich import text as rich_text
from rich import padding as rich_padding
from rich import panel as rich_panel


Lines = typing.List[typing.List[rich_segment.Segment]]
Renderable = str | rich_console.RenderableType


class TerminalLike(typing.Protocol):
    console: rich_console.Console

    def notify_component(self, component: Component) -> None:
        ...


@dataclass(frozen=True)
class ComponentStyle:
    style: rich_style.Style | str | None = None
    panel_style: rich_style.Style | str | None = None
    panel_border_style: rich_style.Style | str | None = None
    panel_box: rich_box.Box | None = None
    panel_title: str | rich_text.Text | None = None
    panel_title_align: rich_align.AlignMethod | None = None
    panel_title_highlight: bool | None = None
    panel_subtitle: str | rich_text.Text | None = None
    panel_subtitle_align: rich_align.AlignMethod | None = None
    panel_padding: int | tuple[int, int] | tuple[int, int, int, int] | None = None
    padding_pad: int | tuple[int, int] | tuple[int, int, int, int] | None = None
    padding_style: rich_style.Style | str | None = None


class Component(ABC):
    def __init__(
        self,
        id: str | None = None,
        component_style: ComponentStyle | None = None,
    ) -> None:
        self.id = id
        self.terminal: TerminalLike | None = None
        self.component_style = component_style

    @abstractmethod
    def render(self, options: rich_console.ConsoleOptions) -> Lines:
        raise NotImplementedError

    def on_key_event(self, event: typing.Any) -> None:
        return

    def on_mouse_event(self, event: typing.Any) -> None:
        return

    def apply_style(self, renderable: Renderable) -> Renderable:
        component_style = self.component_style
        if component_style is None:
            return renderable
        current: Renderable
        style = component_style.style
        if isinstance(renderable, str):
            if style is not None:
                current = rich_text.Text(renderable, style=style)
            else:
                current = rich_text.Text(renderable)
        else:
            current = renderable
            if style is not None and isinstance(current, rich_text.Text):
                current = rich_text.Text(str(current), style=style)
        panel_style = component_style.panel_style
        panel_border_style = component_style.panel_border_style
        panel_box = component_style.panel_box
        panel_title = component_style.panel_title
        panel_title_align = component_style.panel_title_align
        panel_title_highlight = component_style.panel_title_highlight
        panel_subtitle = component_style.panel_subtitle
        panel_subtitle_align = component_style.panel_subtitle_align
        panel_padding = component_style.panel_padding

        use_panel = False
        if (
            panel_style is not None
            or panel_border_style is not None
            or panel_box is not None
            or panel_title is not None
            or panel_title_align is not None
            or panel_subtitle is not None
            or panel_subtitle_align is not None
            or panel_padding is not None
            or (panel_title_highlight is not None and panel_title_highlight)
        ):
            use_panel = True

        if use_panel:
            panel_kwargs: dict[str, typing.Any] = {}
            if panel_title is not None:
                panel_kwargs["title"] = panel_title
            if panel_subtitle is not None:
                panel_kwargs["subtitle"] = panel_subtitle
            if panel_style is not None:
                panel_kwargs["style"] = panel_style
            if panel_border_style is not None:
                panel_kwargs["border_style"] = panel_border_style
            if panel_box is not None:
                panel_kwargs["box"] = panel_box
            if panel_title_align is not None:
                panel_kwargs["title_align"] = panel_title_align
            if panel_subtitle_align is not None:
                panel_kwargs["subtitle_align"] = panel_subtitle_align
            if panel_padding is not None:
                panel_kwargs["padding"] = panel_padding
            if panel_title_highlight is not None:
                panel_kwargs["highlight"] = panel_title_highlight

            current = rich_panel.Panel(
                current,
                **panel_kwargs,
            )

        padding_pad = component_style.padding_pad
        padding_style = component_style.padding_style

        if padding_pad is not None:
            padding_kwargs: dict[str, typing.Any] = {
                "pad": padding_pad,
            }
            if padding_style is not None:
                padding_kwargs["style"] = padding_style

            current = rich_padding.Padding(
                current,
                **padding_kwargs,
            )

        return current
