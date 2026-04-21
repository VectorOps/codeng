from __future__ import annotations

import asyncio

from vocode.manager import proto as manager_proto
from vocode import settings as vocode_settings
from vocode.mcp.service import MCPServiceError

from . import output as command_output
from .base import CommandError, command, option


MCP_SUBCOMMANDS = ("list", "login", "status", "logout", "cancel")


def _get_mcp_settings(server) -> vocode_settings.MCPSettings:
    settings = server.manager.project.settings
    if settings is None or settings.mcp is None or not settings.mcp.enabled:
        raise CommandError("MCP is not configured for this project.")
    return settings.mcp


def _resolve_source_settings(
    server,
    source_name: str,
) -> vocode_settings.MCPSourceSettings:
    settings = _get_mcp_settings(server)
    source_settings = settings.sources.get(source_name)
    if source_settings is None:
        raise CommandError(f"Unknown MCP source '{source_name}'.")
    return source_settings


def _ensure_external_auth_source(
    server,
    source_name: str,
) -> vocode_settings.MCPExternalSourceSettings:
    source_settings = _resolve_source_settings(server, source_name)
    if not isinstance(source_settings, vocode_settings.MCPExternalSourceSettings):
        raise CommandError(
            f"MCP source '{source_name}' does not use external HTTP auth."
        )
    auth_settings = source_settings.auth
    if auth_settings is None or not auth_settings.enabled:
        raise CommandError(f"MCP source '{source_name}' does not require auth.")
    if auth_settings.mode not in {
        vocode_settings.MCPAuthMode.auto,
        vocode_settings.MCPAuthMode.preregistered,
    }:
        raise CommandError(
            f"MCP login is not implemented for auth mode '{auth_settings.mode.value}'."
        )
    return source_settings


def _get_mcp_service(server):
    service = server.manager.project.mcp
    if service is None:
        raise CommandError("MCP service is not initialized.")
    return service


def _source_requires_auth(source_settings: vocode_settings.MCPSourceSettings) -> bool:
    return (
        isinstance(source_settings, vocode_settings.MCPExternalSourceSettings)
        and source_settings.auth is not None
        and source_settings.auth.enabled
    )


def _source_transport_name(source_settings: vocode_settings.MCPSourceSettings) -> str:
    if isinstance(source_settings, vocode_settings.MCPExternalSourceSettings):
        return "external"
    return "stdio"


def _build_mcp_help() -> str:
    return command_output.format_help(
        "MCP commands:",
        [
            ("/mcp status [source]", "Show MCP source status"),
            ("/mcp list [source]", "List MCP tools and source readiness"),
            ("/mcp login <source>", "Authenticate an MCP source"),
            (
                "/mcp logout <source>",
                "Remove stored MCP authentication",
            ),
            ("/mcp cancel", "Cancel active MCP authentication"),
        ],
    )


async def _list_tools_for_source(service, source_name: str):
    cached_tools = service.list_cached_tools(source_name)
    if cached_tools:
        return cached_tools
    session_state = service.get_session_state(source_name)
    if session_state is None:
        return cached_tools
    if not session_state.negotiation.server_capabilities.tools:
        return cached_tools
    try:
        return await service.refresh_tools(source_name)
    except MCPServiceError:
        return cached_tools


async def _build_source_status_lines(
    server,
    source_name: str,
) -> list[str]:
    source_settings = _resolve_source_settings(server, source_name)
    service = _get_mcp_service(server)
    status = await service.authorization_status(source_name)
    cached_tools = await _list_tools_for_source(service, source_name)
    auth_required = _source_requires_auth(source_settings)
    auth_mode = "disabled"
    if (
        isinstance(source_settings, vocode_settings.MCPExternalSourceSettings)
        and source_settings.auth is not None
    ):
        auth_mode = source_settings.auth.mode.value
    lines = [f"MCP source: {source_name}"]
    lines.append(f"Transport: {_source_transport_name(source_settings)}")
    lines.append(f"Auth required: {'yes' if auth_required else 'no'}")
    lines.append(f"Auth mode: {auth_mode}")
    if auth_required:
        lines.append(f"Has token: {'yes' if status.has_token else 'no'}")
    lines.append(f"Session active: {'yes' if status.session_active else 'no'}")
    lines.append(f"Cached tools: {len(cached_tools)}")
    return lines


async def _handle_status(server, args: list[str]) -> None:
    settings = _get_mcp_settings(server)
    if len(args) > 1:
        raise CommandError("Usage: /mcp status [source]")
    if len(args) == 1:
        lines = await _build_source_status_lines(server, args[0])
        lines[0] = command_output.heading(lines[0])
        await command_output.send_rich(server, "\n".join(lines))
        return
    source_names = sorted(settings.sources.keys())
    if not source_names:
        await server.send_text_message("No MCP sources configured.")
        return
    service = _get_mcp_service(server)
    lines = [command_output.heading("MCP sources:")]
    for source_name in source_names:
        source_settings = settings.sources[source_name]
        status = await service.authorization_status(source_name)
        auth_required = _source_requires_auth(source_settings)
        cached_tools = await _list_tools_for_source(service, source_name)
        parts = [
            _source_transport_name(source_settings),
            f"auth required: {'yes' if auth_required else 'no'}",
        ]
        if auth_required:
            parts.append(f"token: {'yes' if status.has_token else 'no'}")
        parts.append(f"session: {'yes' if status.session_active else 'no'}")
        parts.append(f"cached tools: {len(cached_tools)}")
        lines.append(f"  {source_name} - {', '.join(parts)}")
    await command_output.send_rich(server, "\n".join(lines))


def _build_tools_unavailable_text(
    *,
    auth_required: bool,
    has_token: bool,
    session_active: bool,
) -> str:
    if auth_required and not has_token:
        return "unavailable until authentication is configured and a session is started"
    if not session_active:
        return "unavailable until a session is started"
    return "none advertised"


async def _handle_list(server, args: list[str]) -> None:
    settings = _get_mcp_settings(server)
    service = _get_mcp_service(server)
    if len(args) > 1:
        raise CommandError("Usage: /mcp list [source]")
    if len(args) == 1:
        source_names = [args[0]]
        _resolve_source_settings(server, args[0])
    else:
        source_names = sorted(settings.sources.keys())
    if not source_names:
        await server.send_text_message("No MCP sources configured.")
        return
    lines = [command_output.heading("MCP tools:")]
    for index, source_name in enumerate(source_names):
        source_settings = settings.sources[source_name]
        status = await service.authorization_status(source_name)
        auth_required = _source_requires_auth(source_settings)
        cached_tools = await _list_tools_for_source(service, source_name)
        if index > 0:
            lines.append("")
        lines.append(command_output.heading(f"Source: {source_name}"))
        lines.append(f"  Transport: {_source_transport_name(source_settings)}")
        lines.append(f"  Auth required: {'yes' if auth_required else 'no'}")
        if auth_required:
            lines.append(f"  Has token: {'yes' if status.has_token else 'no'}")
        lines.append(f"  Session active: {'yes' if status.session_active else 'no'}")
        if cached_tools:
            lines.append("  Tools:")
            tool_state = "active" if status.session_active else "cached"
            for tool_name in sorted(cached_tools.keys()):
                descriptor = cached_tools[tool_name]
                detail = descriptor.title or descriptor.description
                if detail:
                    lines.append(f"    - {tool_name} ({tool_state}) - {detail}")
                else:
                    lines.append(f"    - {tool_name} ({tool_state})")
            continue
        unavailable = _build_tools_unavailable_text(
            auth_required=auth_required,
            has_token=status.has_token,
            session_active=status.session_active,
        )
        lines.append(f"  Tools: {unavailable}.")
    await command_output.send_rich(server, "\n".join(lines))


@command(
    "mcp",
    description="Manage MCP sources, authentication, and tools",
    params=["[list|status|login|logout|cancel]", "[source]"],
)
@option(0, "args", type=str, splat=True)
async def _mcp(server, args: list[str]) -> None:
    if not args:
        await command_output.send_rich(server, _build_mcp_help())
        return
    action = args[0]
    subcommand_args = args[1:]
    if action == "cancel":
        if subcommand_args:
            raise CommandError("Usage: /mcp cancel")
        session = server.mcp_auth_session
        if session is None:
            raise CommandError("No MCP authentication is currently in progress.")
        cancelled = await session.cancel()
        if not cancelled:
            raise CommandError("No MCP authentication is currently in progress.")
        await command_output.send_success(server, "MCP authentication cancelled.")
        return

    if action not in MCP_SUBCOMMANDS:
        raise CommandError(f"Unknown subcommand '{action}' for /mcp.\nTry: /mcp")

    if action == "status":
        await _handle_status(server, subcommand_args)
        return

    if action == "list":
        await _handle_list(server, subcommand_args)
        return

    if len(subcommand_args) != 1:
        raise CommandError(f"Usage: /mcp {action} <source>")
    source_name = subcommand_args[0]
    source_settings = _ensure_external_auth_source(server, source_name)
    service = _get_mcp_service(server)

    if action == "logout":
        await service.logout(source_name)
        await command_output.send_success(
            server,
            f"Removed stored MCP authentication for {source_name}.",
        )
        return

    if action != "login":
        raise CommandError(f"Unknown subcommand '{action}' for /mcp.\nTry: /mcp")

    if server.mcp_auth_session is not None:
        raise CommandError("MCP authentication is already in progress.")

    session = server.start_mcp_authentication_session(
        lambda: service.login(source_name)
    )
    progress_id = f"mcp-auth:{source_name}"
    await server._emit_progress_start(
        progress_id=progress_id,
        title=f"Authenticating MCP source {source_name}",
        message=None,
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.PULSE,
    )
    await server.emit_progress_update(
        progress_id=progress_id,
        title=f"Authenticating MCP source {source_name}",
        message="Requesting token. Use /mcp cancel to abort.",
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.PULSE,
    )
    try:
        await session.run()
    except asyncio.CancelledError:
        await command_output.send_success(server, "MCP authentication cancelled.")
        return
    except MCPServiceError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        server.clear_mcp_authentication_session()
        await server._emit_progress_end(progress_id=progress_id)

    await command_output.send_success(
        server,
        f"MCP authentication successful for {source_name}.",
    )
