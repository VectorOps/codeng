from __future__ import annotations

from vocode import settings as vocode_settings

from .base import CommandManager, CommandHandler


async def register_workflow_commands(manager: CommandManager) -> None:
    async def list_workflows(server, args: list[str]) -> None:
        project = server.manager.project
        config = project.settings
        if config is None or not isinstance(config, vocode_settings.Settings):
            return
        workflows = sorted(config.workflows.keys())
        if not workflows:
            text = "No workflows configured."
        else:
            text = "Workflows:\n" + "\n".join(f"  - {name}" for name in workflows)
        await server.send_text_message(text)

    await manager.register(
        "workflows",
        list_workflows,
        description="List configured workflows",
    )
