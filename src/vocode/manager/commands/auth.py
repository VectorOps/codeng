from __future__ import annotations

from vocode.manager import proto as manager_proto
from vocode.connect_auth import AuthenticationCancelledError

from .base import CommandError, command, option


AUTH_SUBCOMMANDS = ("login", "status", "verify", "logout", "cancel")
AUTH_PROVIDERS = ("chatgpt",)


@command(
    "auth",
    description="Manage provider authentication",
    params=["<login|status|verify|logout|cancel>", "[provider]"],
)
@option(0, "action", type=str)
@option(1, "args", type=str, splat=True)
async def _auth(server, action: str, args: list[str]) -> None:
    if action == "cancel":
        if args:
            raise CommandError("Usage: /auth cancel")
        session = server.auth_session
        if session is None:
            raise CommandError("No authentication is currently in progress.")
        cancelled = await session.cancel()
        if not cancelled:
            raise CommandError("No authentication is currently in progress.")
        await server.send_text_message("Authentication cancelled.")
        return

    if action not in AUTH_SUBCOMMANDS:
        raise CommandError(
            "Usage: /auth <login|status|verify|logout|cancel> [provider]"
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
            await server.send_text_message(
                f"Authentication is configured for {provider}."
            )
        else:
            await server.send_text_message(
                f"Authentication is not configured for {provider}."
            )
        return

    if action == "verify":
        status = await credentials.authorization_status(provider)
        if not status.is_authorized:
            raise CommandError(
                f"No active authorization for {provider}. Run /auth login {provider}."
            )
        await credentials.resolve(provider)
        await server.send_text_message(f"Authentication verified for {provider}.")
        return

    if action == "logout":
        await credentials.logout(provider)
        await server.send_text_message(f"Authentication removed for {provider}.")
        return

    if action != "login":
        raise CommandError(
            "Usage: /auth <login|status|verify|logout|cancel> [provider]"
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
        await server.send_text_message("Authentication cancelled.")
        return
    finally:
        server._auth_session = None
        await server._emit_progress_end(progress_id=auth_progress_id)

    await server.send_text_message(f"Authentication successful for {provider}.")
