from __future__ import annotations

import asyncio

import pytest

from vocode import settings as vocode_settings
from vocode.manager import (
    autocomplete_providers as _autocomplete_providers,
)  # noqa: F401
from vocode.manager.autocomplete import AutocompleteManager, AutocompleteItem
from vocode.manager.server import UIServer
from vocode.manager import helpers as manager_helpers
from tests.stub_project import StubProject


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
            return [
                AutocompleteItem(
                    title="one",
                    replace_start=0,
                    replace_text="",
                    insert_text="one",
                )
            ]
        return None

    async def provider_two(
        server: object,
        text: str,
        row: int,
        col: int,
    ) -> list[AutocompleteItem] | None:
        _ = server, row, col
        if text:
            return [
                AutocompleteItem(
                    title="two",
                    replace_start=0,
                    replace_text="",
                    insert_text="TWO",
                )
            ]
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
    assert [item.insert_text for item in items] == ["one", "TWO"]


@pytest.mark.asyncio
async def test_auth_autocomplete_provider_suggests_subcommands() -> None:
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    items = await server._autocomplete.get_completions(server, "/auth l", 0, 7)

    assert any(item.insert_text == "login " for item in items)


@pytest.mark.asyncio
async def test_auth_autocomplete_provider_suggests_provider_after_subcommand() -> None:
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    items = await server._autocomplete.get_completions(server, "/auth login ch", 0, 14)

    assert any(item.insert_text == "chatgpt" for item in items)


@pytest.mark.asyncio
async def test_mcp_autocomplete_provider_suggests_subcommands_and_sources() -> None:
    settings = vocode_settings.Settings(
        mcp=vocode_settings.MCPSettings(
            sources={
                "remote": vocode_settings.MCPExternalSourceSettings(
                    url="https://example.test/mcp",
                    auth=vocode_settings.MCPAuthSettings(
                        mode="preregistered",
                        client_id="client-123",
                        client_secret_env="MCP_SECRET",
                    ),
                )
            }
        )
    )
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(settings=settings), endpoint=server_endpoint)

    subcommands = await server._autocomplete.get_completions(server, "/mcp l", 0, 6)
    sources = await server._autocomplete.get_completions(
        server,
        "/mcp login re",
        0,
        13,
    )

    assert any(item.insert_text == "login " for item in subcommands)
    assert any(item.insert_text == "remote" for item in sources)
