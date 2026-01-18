from __future__ import annotations

from vocode import settings as vocode_settings

from .base import CommandManager, CommandHandler, CommandError


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

    async def run_workflow(server, args: list[str]) -> None:
        if not args:
            raise CommandError("Usage: /run <workflow-name>")
        workflow_name = args[0]

        project = server.manager.project
        config = project.settings
        if config is None or not isinstance(config, vocode_settings.Settings):
            raise CommandError("Project settings do not define any workflows.")

        if workflow_name not in config.workflows:
            raise CommandError(f"Unknown workflow '{workflow_name}'.")

        await server.manager.stop_all_runners()
        await server.manager.start_workflow(workflow_name)

    await manager.register(
        "run",
        run_workflow,
        description="Stop all running workflows and start the given workflow",
        params=["<workflow-name>"],
    )

    async def continue_workflow(server, args: list[str]) -> None:
        if args:
            raise CommandError("Usage: /continue")

        try:
            await server.manager.continue_current_runner()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

    await manager.register(
        "continue",
        continue_workflow,
        description="Continue the current stopped workflow",
    )

    async def reset_workflow(server, args: list[str]) -> None:
        if args:
            raise CommandError("Usage: /reset")

        workflow_name = server.manager.project.current_workflow
        if workflow_name is None:
            raise CommandError("No active workflow to reset.")

        await run_workflow(server, [workflow_name])

    await manager.register(
        "reset",
        reset_workflow,
        description="Reset and restart the current workflow",
    )
