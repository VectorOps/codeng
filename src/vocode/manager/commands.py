from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Dict


if TYPE_CHECKING:
    from .server import UIServer


CommandHandler = Callable[["UIServer", str], Awaitable[None]]


class CommandManager:
    def __init__(self) -> None:
        self._commands: Dict[str, CommandHandler] = {}

    async def register(self, name: str, handler: CommandHandler) -> None:
        if name in self._commands:
            raise ValueError(f"Command with name '{name}' already registered.")
        self._commands[name] = handler

    async def unregister(self, name: str) -> bool:
        return self._commands.pop(name, None) is not None

    async def run(self, server: "UIServer", name: str, args: str) -> bool:
        handler = self._commands.get(name)
        if handler is None:
            return False
        await handler(server, args)
        return True
