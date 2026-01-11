from __future__ import annotations

import asyncio

import pytest

from vocode.manager.autocomplete import AutocompleteManager, AutocompleteItem


@pytest.mark.asyncio
async def test_autocomplete_manager_returns_empty_without_providers() -> None:
    manager = AutocompleteManager()
    server = object()
    items = await manager.get_completions(server, "hello", 0, 3)
    assert items == []


@pytest.mark.asyncio
async def test_autocomplete_manager_aggregates_results() -> None:
    manager = AutocompleteManager()

    async def provider_one(
        server: object,
        text: str,
        row: int,
        col: int,
    ) -> list[AutocompleteItem] | None:
        _ = server, row, col
        if text:
            return [AutocompleteItem(title="one")]
        return None

    async def provider_two(
        server: object,
        text: str,
        row: int,
        col: int,
    ) -> list[AutocompleteItem] | None:
        _ = server, row, col
        if text:
            return [AutocompleteItem(title="two", value="TWO")]
        return None

    async def provider_none(
        server: object,
        text: str,
        row: int,
        col: int,
    ) -> list[AutocompleteItem] | None:
        _ = server, text, row, col
        return None

    manager.register(provider_one)
    manager.register(provider_two)
    manager.register(provider_none)

    server = object()
    items = await manager.get_completions(server, "x", 0, 1)
    assert [item.title for item in items] == ["one", "two"]
    assert [item.value for item in items] == [None, "TWO"]
