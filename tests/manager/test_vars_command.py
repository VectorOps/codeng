from __future__ import annotations

import pytest

from vocode import models, state
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer
from vocode.manager import proto as manager_proto
from vocode import settings as vocode_settings
from vocode.vars import VarDef
from tests.stub_project import StubProject
from vocode.manager import autocomplete_providers as autocomplete_providers


@pytest.mark.asyncio
async def test_var_list_shows_variables_and_trims_long_values() -> None:
    settings = vocode_settings.Settings()
    settings.set_var_context(
        {
            "SHORT": "ok",
            "LONG": "x" * 1000,
        }
    )
    settings._set_var_defs(
        {
            "SHORT": VarDef(value="ok"),
            "LONG": VarDef(value="x" * 1000),
        }
    )
    project = StubProject(settings=settings)
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    message = state.Message(role=models.Role.USER, text="/var list")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    assert payload.format == manager_proto.TextMessageFormat.RICH_TEXT
    assert "SHORT" in payload.text
    assert "LONG" in payload.text
    assert "(trimmed)" in payload.text


@pytest.mark.asyncio
async def test_var_set_updates_variable_value() -> None:
    settings = vocode_settings.Settings()
    settings.set_var_context({"HOST": "localhost"})
    settings._set_var_defs({"HOST": VarDef(value="localhost", options=None)})
    project = StubProject(settings=settings)
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    message = state.Message(role=models.Role.USER, text="/var set HOST remote")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    assert payload.format == manager_proto.TextMessageFormat.RICH_TEXT
    assert "HOST" in payload.text
    assert "remote" in payload.text
    assert project.settings.get_variable_value("HOST") == "remote"


@pytest.mark.asyncio
async def test_var_autocomplete_provider_suggests_variable_names() -> None:
    settings = vocode_settings.Settings()
    settings.set_var_context({"HOST": "localhost"})
    settings._set_var_defs({"HOST": VarDef(value="localhost")})
    project = StubProject(settings=settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.var_autocomplete_provider(
        server,
        "/var set H",
        0,
        len("/var set H"),
    )
    assert items is not None
    assert any("HOST" in it.title for it in items)


@pytest.mark.asyncio
async def test_var_autocomplete_provider_suggests_values_from_options() -> None:
    settings = vocode_settings.Settings()
    settings.set_var_context({"HOST": "localhost"})
    settings._set_var_defs(
        {"HOST": VarDef(value="localhost", options=["localhost", "remote"])}
    )
    project = StubProject(settings=settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.var_autocomplete_provider(
        server,
        "/var set HOST r",
        0,
        len("/var set HOST r"),
    )
    assert items is not None
    assert [i.insert_text for i in items] == ["remote"]


@pytest.mark.asyncio
async def test_var_autocomplete_provider_inserts_raw_value_for_non_string_options() -> (
    None
):
    settings = vocode_settings.Settings()
    settings.set_var_context({"PORT": 8000})
    settings._set_var_defs({"PORT": VarDef(value=8000, options=[8000, 9000])})
    project = StubProject(settings=settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.var_autocomplete_provider(
        server,
        "/var set PORT 9",
        0,
        len("/var set PORT 9"),
    )
    assert items is not None
    assert [i.insert_text for i in items] == ["9000"]


@pytest.mark.asyncio
async def test_var_autocomplete_provider_returns_up_to_ten_items() -> None:
    settings = vocode_settings.Settings()
    settings.set_var_context({"HOST": "v0"})
    settings._set_var_defs(
        {
            "HOST": VarDef(
                value="v0",
                options=[f"v{i}" for i in range(25)],
            )
        }
    )
    project = StubProject(settings=settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.var_autocomplete_provider(
        server,
        "/var set HOST v",
        0,
        len("/var set HOST v"),
    )
    assert items is not None
    assert len(items) == 10
