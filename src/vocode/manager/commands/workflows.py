from __future__ import annotations

from typing import Optional
from uuid import UUID

from vocode import state as vocode_state
from vocode import settings as vocode_settings
from vocode.runner import base as runner_base
from vocode.runner.executors.llm import llm as llm_executor_mod
from vocode.runner.executors.llm import models as llm_models
from vocode.runner.executors.llm.compaction import service as llm_compaction_service

from . import output as command_output
from .base import CommandManager, CommandError


def _resolve_current_node_execution(
    frame,
) -> Optional[vocode_state.NodeExecution]:
    execution = frame.runner.execution
    stats = frame.last_stats
    if stats is not None and stats.current_node_execution_id is not None:
        current_execution = execution.node_executions.get(
            stats.current_node_execution_id
        )
        if current_execution is not None:
            return current_execution
    if execution.node_executions:
        return list(execution.node_executions.values())[-1]
    return None


def _format_compaction_stats(
    *,
    node_name: str,
    prompt_messages_before: int,
    prompt_messages_after: int,
    retained_messages: int,
    persisted_token_percentage: float,
    persisted_token_percentage_defaulted: bool,
    preparation,
    summary_state,
) -> str:
    percentage_text = f"{persisted_token_percentage:g}%"
    if persisted_token_percentage_defaulted:
        percentage_text += " (default)"
    lines = [
        command_output.heading("Context compaction"),
        command_output.success("Compaction complete."),
        f"  Node: {command_output.escape(node_name)}",
        (
            "  Prompt-visible messages: "
            f"{prompt_messages_before} -> {prompt_messages_after}"
        ),
        f"  Persisted token target: {percentage_text}",
        f"  Retained recent messages: {retained_messages}",
    ]
    if preparation.estimated_context_tokens > 0:
        lines.append(
            "  Estimated context tokens: " f"{preparation.estimated_context_tokens}"
        )
    if summary_state is not None and summary_state.summary_input_tokens is not None:
        summary_line = f"  Summary tokens: {summary_state.summary_input_tokens}"
        if summary_state.prompt_tokens_after is not None:
            summary_line += f" -> {summary_state.prompt_tokens_after}"
        lines.append(summary_line)
    return "\n".join(lines)


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

    async def compact_current_llm_node(server, args: list[str]) -> None:
        if len(args) > 1:
            raise CommandError("Usage: /compact [persist-percent]")

        stack = server.manager.runner_stack
        if not stack:
            raise CommandError("No active runner.")
        frame = stack[-1]
        runner = frame.runner
        if runner.status not in (
            vocode_state.RunnerStatus.WAITING_INPUT,
            vocode_state.RunnerStatus.STOPPED,
        ):
            raise CommandError(
                "Current runner must be paused (waiting for input) or stopped."
            )

        current_execution = _resolve_current_node_execution(frame)
        if current_execution is None:
            raise CommandError("No current node execution to compact.")

        node_model = runner.workflow.graph.node_by_name.get(current_execution.node)
        if not isinstance(node_model, llm_models.LLMNode):
            raise CommandError("Current node is not an LLM node.")

        executor = runner._executors.get(current_execution.node)
        if not isinstance(executor, llm_executor_mod.LLMExecutor):
            raise CommandError("Current node does not support manual compaction.")

        prompt_messages = list(
            llm_compaction_service.collect_prompt_messages(
                server.manager.project.history,
                current_execution,
            )
        )
        prompt_messages_count = len(prompt_messages)
        if prompt_messages_count < 2:
            raise CommandError(
                "Current LLM node does not have enough prompt-visible messages to compact."
            )

        persisted_token_percentage_defaulted = not args
        if args:
            try:
                persisted_token_percentage = float(args[0])
            except ValueError as exc:
                raise CommandError(
                    "Persist percent must be a number between 0 and 100."
                ) from exc
        else:
            persisted_token_percentage = 10.0

        if persisted_token_percentage <= 0 or persisted_token_percentage >= 100:
            raise CommandError(
                "Persist percent must be greater than 0 and less than 100."
            )

        preparation = executor._prepare_compaction(
            runner_base.ExecutorInput(
                execution=current_execution,
                run=runner.execution,
            )
        )
        settings = preparation.settings.model_copy(
            update={"keep_recent_ratio": persisted_token_percentage / 100.0}
        )
        preparation = preparation.model_copy(
            update={
                "should_compact": True,
                "settings": settings,
            }
        )

        compaction_result = await llm_compaction_service.compact_execution_history(
            server.manager.project.history,
            server.manager.project.credentials,
            current_execution,
            preparation,
        )
        if compaction_result is None:
            raise CommandError(
                "Unable to compact the current LLM node state with the requested persisted token percentage."
            )
        compaction_step, mutation_result = compaction_result

        updated_prompt_messages = list(
            llm_compaction_service.collect_prompt_messages(
                server.manager.project.history,
                current_execution,
            )
        )
        await server.emit_history_mutation(frame, mutation_result)
        summary_state = llm_compaction_service.get_compaction_summary_state(
            compaction_step,
        )
        await command_output.send_rich(
            server,
            _format_compaction_stats(
                node_name=current_execution.node,
                prompt_messages_before=prompt_messages_count,
                prompt_messages_after=len(updated_prompt_messages),
                retained_messages=max(0, len(updated_prompt_messages) - 1),
                persisted_token_percentage=persisted_token_percentage,
                persisted_token_percentage_defaulted=(
                    persisted_token_percentage_defaulted
                ),
                preparation=preparation,
                summary_state=summary_state,
            ),
        )

    await manager.register(
        "compact",
        compact_current_llm_node,
        description="Compact the current paused or stopped LLM node state",
        params=["[persist-percent]"],
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
