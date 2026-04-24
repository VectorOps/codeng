from __future__ import annotations

from uuid import UUID

from vocode import settings as vocode_settings

from . import output as command_output
from .base import CommandManager, CommandHandler, CommandError


async def register_workflow_commands(manager: CommandManager) -> None:
    async def list_workflows(server, args: list[str]) -> None:
        project = server.manager.project
        config = project.settings
        if config is None or not isinstance(config, vocode_settings.Settings):
            return
        workflows = sorted(config.workflows.keys())
        if not workflows:
            await command_output.send_warning(server, "No workflows configured.")
        else:
            text = (
                command_output.heading("Workflows:")
                + "\n"
                + "\n".join(f"  - {name}" for name in workflows)
            )
            await command_output.send_rich(server, text)

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

        stack = server.manager.runner_stack
        workflow_name = stack[0].workflow_name if stack else None
        if workflow_name is None:
            workflow_name = server.manager.project.current_workflow
        if workflow_name is None:
            workflow_name = server.manager.project.last_root_workflow
        if workflow_name is None:
            raise CommandError("No active workflow to reset.")

        await run_workflow(server, [workflow_name])

    await manager.register(
        "reset",
        reset_workflow,
        description="Reset and restart the current workflow",
    )

    async def exit_command(server, args: list[str]) -> None:
        if args:
            raise CommandError("Usage: /exit")
        await server.stop()

    await manager.register(
        "exit",
        exit_command,
        description="Exit the TUI session",
    )

    async def branch_command(server, args: list[str]) -> None:
        if not args:
            raise CommandError("Usage: /branch <list|switch> [args]")
        subcommand = args[0]
        runner = server.manager.current_runner
        if runner is None:
            raise CommandError("No active runner.")
        execution = runner.execution
        history = server.manager.project.history

        if subcommand == "list":
            if len(args) != 1:
                raise CommandError("Usage: /branch list")
            summaries = history.list_branch_summaries(execution)
            lines = [command_output.heading("Branches:")]
            for summary in summaries:
                label = summary.label or str(summary.id)
                active = " active" if summary.is_active else ""
                lines.append(f"  - {summary.id}: {label}{active}")
            await command_output.send_rich(server, "\n".join(lines))
            return

        if subcommand == "switch":
            if len(args) != 2:
                raise CommandError("Usage: /branch switch <branch-id>")
            try:
                branch_id = UUID(args[1])
            except ValueError as exc:
                raise CommandError("Invalid branch id.") from exc
            result = history.switch_branch(execution, branch_id)
            frame = server.manager.runner_stack[-1]
            await server.emit_history_mutation(frame, result)
            return

        raise CommandError("Usage: /branch <list|switch> [args]")

    await manager.register(
        "branch",
        branch_command,
        description="Inspect and switch workflow branches",
        params=["<list|switch>", "[branch-id]"],
    )
