from __future__ import annotations

import pytest

from vocode.manager import autocomplete as manager_autocomplete


class _DummyServer:
    pass


@pytest.mark.asyncio
async def test_filter_autocomplete_items_for_text_filters_exact_matches() -> None:
    text = "hello"
    items = [
        manager_autocomplete.AutocompleteItem(
            title="hello",
            replace_start=0,
            replace_text="hello",
            insert_text="hello",
        ),
        manager_autocomplete.AutocompleteItem(
            title="hello-world",
            replace_start=0,
            replace_text="he",
            insert_text="hello-world",
        ),
    ]
    filtered = manager_autocomplete.filter_autocomplete_items_for_text(items, text)
    assert [item.title for item in filtered] == ["hello-world"]


@pytest.mark.asyncio
async def test_autocomplete_manager_filters_exact_match_items_from_providers() -> None:
    async def provider_same(
        server: _DummyServer,
        text: str,
        row: int,
        col: int,
    ) -> list[manager_autocomplete.AutocompleteItem]:
        return [
            manager_autocomplete.AutocompleteItem(
                title="same",
                replace_start=0,
                replace_text=text,
                insert_text=text,
            )
        ]

    async def provider_mixed(
        server: _DummyServer,
        text: str,
        row: int,
        col: int,
    ) -> list[manager_autocomplete.AutocompleteItem]:
        return [
            manager_autocomplete.AutocompleteItem(
                title="same",
                replace_start=0,
                replace_text=text,
                insert_text=text,
            ),
            manager_autocomplete.AutocompleteItem(
                title="other",
                replace_start=0,
                replace_text="",
                insert_text=text + "-suffix",
            ),
        ]

    manager = manager_autocomplete.AutocompleteManager()
    manager.register(provider_same)
    manager.register(provider_mixed)

    text = "value"
    items = await manager.get_completions(_DummyServer(), text, 0, len(text))
    titles = [item.title for item in items]
    inserts = [item.insert_text for item in items]

    assert titles == ["other"]
    assert inserts == ["value-suffix"]