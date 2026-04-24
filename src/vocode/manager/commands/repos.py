from __future__ import annotations

from pathlib import Path

from . import output as command_output
from .base import CommandError, command, option


USAGE = "Usage: /repo <list|add|refresh|refresh_all> [args]"

REPO_HELP = (
    "Repository (know) commands:",
    [
        (
            "/repo list",
            "List repositories associated with the current know project.",
        ),
        ("/repo add <name> <path>", "Add a repository and index it."),
        (
            "/repo refresh <name>",
            "Refresh (re-index) the given repository.",
        ),
        ("/repo refresh_all", "Refresh (re-index) all repositories."),
    ],
)


def _ensure_know_enabled(server) -> None:
    project = server.manager.project
    settings = project.settings
    if settings is None or not settings.know_enabled:
        raise CommandError("Knowledge base (know) is disabled.")
    if settings.know is None:
        raise CommandError("Knowledge base (know) is not configured.")
    try:
        _ = project.know.pm
    except Exception as exc:
        raise CommandError("Knowledge base (know) is not initialized.") from exc


async def _send_repos_list(server) -> None:
    _ensure_know_enabled(server)

    pm = server.manager.project.know.pm
    repo_ids = list(pm.repo_ids)
    if not repo_ids:
        await server.send_text_message("No repos associated with this project.")
        return

    repos = await pm.data.repo.get_by_ids(repo_ids)
    if not repos:
        await server.send_text_message("No repos found.")
        return

    lines: list[str] = [command_output.heading("Repos:")]
    for repo in sorted(repos, key=lambda r: r.name):
        root = repo.root_path or ""
        suffix = f" - {root}" if root else ""
        lines.append(f"  - {repo.name}{suffix}")

    await command_output.send_rich(server, "\n".join(lines))


@command(
    "repo",
    description="Manage knowlt repositories",
    params=["<list|add|refresh|refresh_all>", "..."],
)
@option(0, "args", type=str, splat=True)
async def _repo(server, args: list[str]) -> None:
    if not args:
        await command_output.send_rich(server, command_output.format_help(*REPO_HELP))
        return

    sub = args[0]
    rest = args[1:]

    if sub == "list":
        if rest:
            raise CommandError("Usage: /repo list")
        await _send_repos_list(server)
        return

    if sub == "add":
        if len(rest) != 2:
            raise CommandError("Usage: /repo add <name> <path>")
        _ensure_know_enabled(server)
        name = rest[0]
        path = str(Path(rest[1]).expanduser())
        pm = server.manager.project.know.pm
        repo = await pm.add_repo_path(name, path)
        await server.refresh_know_repo_with_progress(repo)
        await command_output.send_success(server, f"Repo added: {name} -> {path}")
        return

    if sub == "refresh":
        if len(rest) != 1:
            raise CommandError("Usage: /repo refresh <name>")
        _ensure_know_enabled(server)
        name = rest[0]
        pm = server.manager.project.know.pm
        repo = await pm.data.repo.get_by_name(name)
        if repo is None:
            raise CommandError(f"Unknown repo '{name}'.")
        await server.refresh_know_repo_with_progress(repo)
        await command_output.send_success(server, f"Repo refreshed: {name}")
        return

    if sub in {"refresh_all", "refresh-all"}:
        if rest:
            raise CommandError("Usage: /repo refresh_all")
        _ensure_know_enabled(server)
        await server.refresh_know_all_with_progress()
        await command_output.send_success(server, "All repos refreshed.")
        return

    raise CommandError(USAGE)
