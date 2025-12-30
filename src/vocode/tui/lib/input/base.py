from __future__ import annotations

import asyncio
import inspect
import typing
from dataclasses import dataclass


KeyAction = typing.Literal["down", "up"]
MouseAction = typing.Literal["move", "down", "up", "scroll"]
MouseButton = typing.Literal["left", "middle", "right", "x1", "x2", "none"]


@dataclass(frozen=True)
class KeyEvent:
    action: KeyAction
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    text: typing.Optional[str] = None


@dataclass(frozen=True)
class PasteEvent:
    text: str


@dataclass(frozen=True)
class MouseEvent:
    action: MouseAction
    x: int
    y: int
    button: MouseButton = "none"
    shift: bool = False
    alt: bool = False
    ctrl: bool = False
    scroll: int = 0


@dataclass(frozen=True)
class ResizeEvent:
    width: int
    height: int


InputEvent = typing.Union[KeyEvent, PasteEvent, MouseEvent, ResizeEvent]
EventSubscriber = typing.Callable[[InputEvent], typing.Awaitable[None] | None]


class InputHandler:
    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def clear_subscribers(self) -> None:
        self._subscribers.clear()

    def publish(self, event: InputEvent) -> None:
        if not self._subscribers:
            return
        loop = asyncio.get_running_loop()
        for subscriber in list(self._subscribers):
            result = subscriber(event)
            if inspect.isawaitable(result):
                loop.create_task(typing.cast(typing.Awaitable[None], result))

    async def run(self) -> None:
        raise NotImplementedError
