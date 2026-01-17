from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable
from typing import ClassVar, Optional
import dataclasses

if TYPE_CHECKING:
    from .server import UIServer


@dataclasses.dataclass
class AutocompleteItem:
    title: str
    replace_start: int
    replace_text: str
    insert_text: str


AutocompleteProvider = Callable[
    ["UIServer", str, int, int], Awaitable[Optional[list[AutocompleteItem]]]
]


class AutocompleteManager:
    _default_providers: ClassVar[list[AutocompleteProvider]] = []

    def __init__(self) -> None:
        self._providers: list[AutocompleteProvider] = list(self._default_providers)

    @classmethod
    def register_default(cls, provider: AutocompleteProvider) -> AutocompleteProvider:
        cls._default_providers.append(provider)
        return provider

    def register(self, provider: AutocompleteProvider) -> None:
        self._providers.append(provider)

    async def get_completions(
        self,
        server: "UIServer",
        text: str,
        row: int,
        col: int,
    ) -> list[AutocompleteItem]:
        results: list[AutocompleteItem] = []
        for provider in self._providers:
            items = await provider(server, text, row, col)
            if items is None:
                continue
            results.extend(items)
        return results
