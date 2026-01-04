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


_PANEL_KWARG_MAP: typing.Final[dict[str, str]] = {
    "panel_title": "title",
    "panel_subtitle": "subtitle",
    "panel_style": "style",
    "panel_border_style": "border_style",
    "panel_box": "box",
    "panel_title_align": "title_align",
    "panel_subtitle_align": "subtitle_align",
    "panel_padding": "padding",
    "panel_title_highlight": "highlight",
}


def _apply_base_style(
    renderable: Renderable,
    style: rich_style.Style | str | None,
) -> Renderable:
    if style is None:
        if isinstance(renderable, str):
            return rich_text.Text(renderable)
        return renderable
    if isinstance(renderable, str):
        return rich_text.Text(renderable, style=style)
    if isinstance(renderable, rich_text.Text):
        return rich_text.Text(str(renderable), style=style)
    return renderable


def _build_panel_kwargs(component_style: "ComponentStyle") -> dict[str, typing.Any]:
    kwargs: dict[str, typing.Any] = {}
    for field_name, kwarg_name in _PANEL_KWARG_MAP.items():
        if not hasattr(component_style, field_name):
            continue
        value = getattr(component_style, field_name)
        if value is not None:
            kwargs[kwarg_name] = value
    return kwargs


class TerminalLike(typing.Protocol):
    console: rich_console.Console

    def notify_component(self, component: Component) -> None: ...


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
    margin_bottom: int | None = None


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
        style = self.component_style
        if style is None:
            return renderable

        current = _apply_base_style(renderable, style.style)

        panel_kwargs = _build_panel_kwargs(style)
        if panel_kwargs:
            current = rich_panel.Panel(current, **panel_kwargs)

        if style.padding_pad is not None:
            padding_kwargs: dict[str, typing.Any] = {"pad": style.padding_pad}
            if style.padding_style is not None:
                padding_kwargs["style"] = style.padding_style
            current = rich_padding.Padding(current, **padding_kwargs)

        if style.margin_bottom is not None and style.margin_bottom > 0:
            current = rich_padding.Padding(
                current,
                pad=(0, 0, style.margin_bottom, 0),
            )

        return current
