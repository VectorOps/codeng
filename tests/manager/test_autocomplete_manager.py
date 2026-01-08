from __future__ import annotations

import asyncio

import pytest

from vocode.manager.autocomplete import AutocompleteManager


@pytest.mark.asyncio
async def test_autocomplete_manager_returns_empty_without_providers() -> None:
    manager = AutocompleteManager()
    items = await manager.get_completions("hello", 3)
    assert items == []


@pytest.mark.asyncio
async def test_autocomplete_manager_aggregates_results() -> None:
    manager = AutocompleteManager()

    async def provider_one(text: str, cursor: int) -> list[str] | None:
        _ = cursor
        if text:
            return ["one"]
        return None

    async def provider_two(text: str, cursor: int) -> list[str] | None:
        _ = cursor
        if text:
            return ["two"]
        return None

    async def provider_none(text: str, cursor: int) -> list[str] | None:
        _ = text, cursor
        return None

    manager.register(provider_one)
    manager.register(provider_two)
    manager.register(provider_none)

    items = await manager.get_completions("x", 1)
    assert items == ["one", "two"]
