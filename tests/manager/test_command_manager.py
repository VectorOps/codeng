from __future__ import annotations

import asyncio

import pytest

from vocode import models, state
from vocode.manager import helpers as manager_helpers
from vocode.manager import proto as manager_proto
from vocode.manager.commands import CommandManager, command, option
from vocode.manager.server import UIServer
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_command_manager_register_and_run() -> None:
    calls: list[tuple[UIServer, str]] = []

    async def handler(server: UIServer, args: str) -> None:
        calls.append((server, args))

    manager = CommandManager()
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    await manager.register("test", handler)
    handled = await manager.run(server, "test", "arg1")

    assert handled is True
    assert calls == [(server, "arg1")]


@pytest.mark.asyncio
async def test_command_manager_unknown_command_returns_false() -> None:
    manager = CommandManager()
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.run(server, "missing", "")
    assert handled is False


@pytest.mark.asyncio
async def test_command_manager_parse_args_with_quotes() -> None:
    manager = CommandManager()
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    received: list[list[str]] = []

    async def handler(srv: UIServer, args: list[str], raw: str) -> None:  # type: ignore[unused-ignore]
        received.append(list(args))

    await manager.register_invoker("echo", handler)

    handled = await manager.execute(server, 'echo one "two words" three')

    assert handled is True
    assert received == [["one", "two words", "three"]]


@pytest.mark.asyncio
async def test_command_manager_execute_reports_syntax_error() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.execute(server, 'echo "unterminated')

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "Command error"
    assert "Invalid command syntax" in (payload.subtitle or "")


@command("echo2")
@option(0, type=str)
async def _echo2(server: UIServer, text: str) -> None:
    packet = manager_proto.InputPromptPacket(title="echo2", subtitle=text)
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_success() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    declarative = _echo2
    await declarative.register(manager)

    handled = await manager.execute(server, "echo2 hello")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "echo2"
    assert payload.subtitle == "hello"


@command("need-int")
@option(0, type=int)
async def _need_int(server: UIServer, value: int) -> None:
    packet = manager_proto.InputPromptPacket(title="need-int", subtitle=str(value))
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_validation_error() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    declarative = _need_int
    await declarative.register(manager)

    handled = await manager.execute(server, "need-int not-an-int")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "Command error"
    assert "Invalid value for 'value' at position 1" in (payload.subtitle or "")


@command("splat-echo")
@option(0, type=str, splat=True)
async def _splat_echo(server: UIServer, items: list[str]) -> None:
    joined = ",".join(items)
    packet = manager_proto.InputPromptPacket(title="splat-echo", subtitle=joined)
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_splat() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    declarative = _splat_echo
    await declarative.register(manager)

    handled = await manager.execute(server, "splat-echo one two three")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "splat-echo"
    assert payload.subtitle == "one,two,three"


@pytest.mark.asyncio
async def test_uiserver_executes_registered_command() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    received: list[manager_proto.InputPromptPacket] = []

    async def handler(srv: UIServer, args: str) -> None:
        packet = manager_proto.InputPromptPacket(title="cmd", subtitle=args)
        await srv.send_packet(packet)

    await server.commands.register("echo", handler)

    message = state.Message(role=models.Role.USER, text="/echo hello")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(payload, manager_proto.InputPromptPacket)
    received.append(payload)

    assert len(received) == 1
    assert received[0].title == "cmd"
    assert received[0].subtitle == "hello"


@pytest.mark.asyncio
async def test_uiserver_unknown_command_sends_error() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/unknown")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "Unknown command"
    assert payload.subtitle == "Unknown command: /unknown"
