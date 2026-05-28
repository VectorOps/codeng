from __future__ import annotations

from vocode.manager import proto as manager_proto
from vocode.auth import AuthenticationCancelledError

from . import output as command_output
from .base import CommandError, command, option


AUTH_SUBCOMMANDS = ("login", "status", "verify", "logout", "cancel", "profile")
AUTH_PROFILE_SUBCOMMANDS = ("list", "switch", "add", "delete")
AUTH_PROVIDERS = ("chatgpt",)


def _build_auth_profile_help() -> str:
    return command_output.format_help(
        "Authentication profile commands:",
        [
            ("/auth profile list", "List configured authentication profiles"),
            ("/auth profile switch <name>", "Switch the active authentication profile"),
            ("/auth profile add <name>", "Add an authentication profile"),
            ("/auth profile delete <name>", "Delete an authentication profile"),
        ],
    )


async def _handle_auth_profile(server, args: list[str]) -> None:
    credentials = server.manager.project.credentials
    if not args:
        await command_output.send_rich(server, _build_auth_profile_help())
        return
    action = args[0]
    if action not in AUTH_PROFILE_SUBCOMMANDS:
        raise CommandError(
            "Unknown subcommand '%s' for /auth profile.\nTry: /auth profile" % action
        )
    if action == "list":
        if len(args) != 1:
            raise CommandError("Usage: /auth profile list")
        active_profile = await credentials.get_active_profile()
        profiles = await credentials.list_profiles()
        lines = [command_output.heading("Authentication profiles:")]
        for index, profile in enumerate(profiles, start=1):
            label = profile
            if profile == active_profile:
                label = f"{label} (active)"
            lines.append(f"  {index}. {command_output.escape(label)}")
        await command_output.send_rich(server, "\n".join(lines))
        return
    if len(args) != 2:
        raise CommandError(f"Usage: /auth profile {action} <name>")
    profile_name = args[1]
    if action == "add":
        created = await credentials.add_profile(profile_name)
        if not created:
            await command_output.send_warning(
                server,
                f"Profile '{profile_name}' already exists.",
            )
            return
        await command_output.send_success(
            server,
            f"Added authentication profile '{profile_name}'.",
        )
        return
    if action == "switch":
        try:
            changed = await credentials.switch_profile(profile_name)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not changed:
            await command_output.send_warning(
                server,
                f"Profile '{profile_name}' is already active.",
            )
            return
        await command_output.send_success(
            server,
            f"Switched authentication profile to '{profile_name}'.",
        )
        return
    try:
        deleted, active_profile = await credentials.delete_profile(profile_name)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    if not deleted:
        await command_output.send_warning(
            server,
            f"Profile '{profile_name}' does not exist.",
        )
        return
    if active_profile == "default":
        await command_output.send_success(
            server,
            f"Deleted authentication profile '{profile_name}' and switched to 'default'.",
        )
        return
    await command_output.send_success(
        server,
        f"Deleted authentication profile '{profile_name}'.",
    )


@command(
    "auth",
    description="Manage provider authentication",
    params=["<login|status|verify|logout|cancel|profile>", "[provider]"],
)
@option(0, "action", type=str)
@option(1, "args", type=str, splat=True)
async def _auth(server, action: str, args: list[str]) -> None:
    if action == "profile":
        await _handle_auth_profile(server, args)
        return
    if action == "cancel":
        if args:
            raise CommandError("Usage: /auth cancel")
        session = server.auth_session
        if session is None:
            raise CommandError("No authentication is currently in progress.")
        cancelled = await session.cancel()
        if not cancelled:
            raise CommandError("No authentication is currently in progress.")
        await command_output.send_success(server, "Authentication cancelled.")
        return

    if action not in AUTH_SUBCOMMANDS:
        raise CommandError(
            "Usage: /auth <login|status|verify|logout|cancel|profile> [provider]"
        )
    if len(args) != 1:
        raise CommandError("Usage: /auth <login|status|verify|logout> <provider>")
    provider = args[0]
    if provider != "chatgpt":
        raise CommandError("Only provider 'chatgpt' is currently supported.")

    credentials = server.manager.project.credentials

    if action == "status":
        status = await credentials.authorization_status(provider)
        if status.is_authorized:
            await command_output.send_success(
                server,
                f"Authentication is configured for {provider}.",
            )
        else:
            await command_output.send_warning(
                server,
                f"Authentication is not configured for {provider}.",
            )
        return

    if action == "verify":
        status = await credentials.authorization_status(provider)
        if not status.is_authorized:
            raise CommandError(
                f"No active authorization for {provider}. Run /auth login {provider}."
            )
        await credentials.resolve(provider)
        await command_output.send_success(
            server,
            f"Authentication verified for {provider}.",
        )
        return

    if action == "logout":
        await credentials.logout(provider)
        await command_output.send_success(
            server,
            f"Authentication removed for {provider}.",
        )
        return

    if action != "login":
        raise CommandError(
            "Usage: /auth <login|status|verify|logout|cancel|profile> [provider]"
        )

    if server.auth_session is not None:
        raise CommandError("Authentication is already in progress.")

    session = server.start_authentication_session(provider)
    auth_progress_id = f"auth:{provider}"
    await server._emit_progress_start(
        progress_id=auth_progress_id,
        title=f"Authenticating {provider}",
        message=None,
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.PULSE,
    )
    await server.emit_progress_update(
        progress_id=auth_progress_id,
        title=f"Authenticating {provider}",
        message="Waiting for browser login. Use /auth cancel to abort.",
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.PULSE,
    )
    try:
        await session.run()
    except AuthenticationCancelledError:
        await command_output.send_success(server, "Authentication cancelled.")
        return
    finally:
        server._auth_session = None
        await server._emit_progress_end(progress_id=auth_progress_id)

    await command_output.send_success(
        server,
        f"Authentication successful for {provider}.",
    )
