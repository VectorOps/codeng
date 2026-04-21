from __future__ import annotations

import asyncio

from vocode.manager import proto as manager_proto
from vocode import settings as vocode_settings
from vocode.mcp.service import MCPServiceError

from .base import CommandError, command, option


MCP_SUBCOMMANDS = ("login", "status", "logout", "cancel")


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


@command(
    "mcp",
    description="Manage MCP source authentication",
    params=["<login|status|logout|cancel>", "[source]"],
)
@option(0, "action", type=str)
@option(1, "args", type=str, splat=True)
async def _mcp(server, action: str, args: list[str]) -> None:
    if action == "cancel":
        if args:
            raise CommandError("Usage: /mcp cancel")
        session = server.mcp_auth_session
        if session is None:
            raise CommandError("No MCP authentication is currently in progress.")
        cancelled = await session.cancel()
        if not cancelled:
            raise CommandError("No MCP authentication is currently in progress.")
        await server.send_text_message("MCP authentication cancelled.")
        return

    if action not in MCP_SUBCOMMANDS:
        raise CommandError("Usage: /mcp <login|status|logout|cancel> [source]")
    if len(args) != 1:
        raise CommandError("Usage: /mcp <login|status|logout> <source>")
    source_name = args[0]
    source_settings = _ensure_external_auth_source(server, source_name)
    service = _get_mcp_service(server)

    if action == "status":
        status = await service.authorization_status(source_name)
        mode_value = (
            source_settings.auth.mode.value
            if source_settings.auth is not None
            else "disabled"
        )
        lines = [f"MCP source: {source_name}"]
        lines.append(f"Transport: external")
        lines.append(f"Auth mode: {mode_value}")
        lines.append(f"Has token: {'yes' if status.has_token else 'no'}")
        lines.append(f"Session active: {'yes' if status.session_active else 'no'}")
        await server.send_text_message("\n".join(lines))
        return

    if action == "logout":
        await service.logout(source_name)
        await server.send_text_message(
            f"Removed stored MCP authentication for {source_name}."
        )
        return

    if action != "login":
        raise CommandError("Usage: /mcp <login|status|logout|cancel> [source]")

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
        await server.send_text_message("MCP authentication cancelled.")
        return
    except MCPServiceError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        server.clear_mcp_authentication_session()
        await server._emit_progress_end(progress_id=progress_id)

    await server.send_text_message(f"MCP authentication successful for {source_name}.")
