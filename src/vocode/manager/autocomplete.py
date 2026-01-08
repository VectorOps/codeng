from __future__ import annotations


from typing import Awaitable, Callable, Optional


AutocompleteProvider = Callable[[str, int], Awaitable[Optional[list[str]]]]


class AutocompleteManager:
    def __init__(self) -> None:
        self._providers: list[AutocompleteProvider] = []

    def register(self, provider: AutocompleteProvider) -> None:
        self._providers.append(provider)

    async def get_completions(self, text: str, cursor: int) -> list[str]:
        results: list[str] = []
        for provider in self._providers:
            items = await provider(text, cursor)
            if items is None:
                continue
            results.extend(items)
        return results
