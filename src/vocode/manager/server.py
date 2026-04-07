from __future__ import annotations

import logging
import asyncio
from typing import Optional, cast
import time
import uuid

from vocode import settings as vocode_settings
from vocode import models
from vocode import state
from vocode.logger import get_log_manager_internal, init_log_manager, logger
from vocode.project import Project
from vocode.runner import proto as runner_proto
from vocode.connect_auth import ServerAuthenticationSession

from .base import BaseManager, RunnerFrame
from .helpers import BaseEndpoint, IncomingPacketRouter, RpcHelper
from . import proto as manager_proto
from .autocomplete import AutocompleteManager
from .commands import CommandManager
from .commands import workflows as workflow_commands


class UIServer:
    manager_proto = manager_proto

    def __init__(
        self,
        project: Project,
        endpoint: BaseEndpoint,
        name: str = "ui-server",
    ) -> None:
        self._endpoint = endpoint
        self._rpc = RpcHelper(self._endpoint.send, name)
        self._router = IncomingPacketRouter(self._rpc, name)
        self._manager = BaseManager(
            project=project,
            run_event_listener=self.on_runner_event,
        )
        self._status = manager_proto.UIServerStatus.IDLE
        self._push_msg_id = 0
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._started = False
        self._autocomplete = AutocompleteManager()
        self._commands = CommandManager()
        self._log_manager = init_log_manager()
        self._ui_input_channel_id = f"ui-server:{name}"
        self._last_tool_confirmation_runner_id: Optional[str] = None
        self._last_tool_confirmation_step: Optional[state.Step] = None
        self._auth_session: Optional[ServerAuthenticationSession] = None
        self._progress_last_sent_at_by_id: dict[str, float] = {}
        self._know_repo_label_by_id: dict[str, str] = {}
        self._emit_branch_packets = False

        self._router.register(
            manager_proto.BasePacketKind.USER_INPUT,
            self._on_user_input_packet,
        )
        self._router.register(
            manager_proto.BasePacketKind.AUTOCOMPLETE_REQ,
            self._on_autocomplete_packet,
        )
        self._router.register(
            manager_proto.BasePacketKind.STOP_REQ,
            self._on_stop_packet,
        )
        self._router.register(
            manager_proto.BasePacketKind.LOG_REQ,
            self._on_log_req_packet,
        )

    def _next_packet_id(self) -> int:
        self._push_msg_id += 1
        return self._push_msg_id

    def _apply_logging_settings(self) -> None:
        project_settings = self._manager.project.settings
        if project_settings is None:
            return

        logging_settings = project_settings.logging
        if logging_settings is None:
            logging_settings = vocode_settings.LoggingSettings()

        level_map = {
            vocode_settings.LogLevel.debug: logging.DEBUG,
            vocode_settings.LogLevel.info: logging.INFO,
            vocode_settings.LogLevel.warning: logging.WARNING,
            vocode_settings.LogLevel.error: logging.ERROR,
            vocode_settings.LogLevel.critical: logging.CRITICAL,
            vocode_settings.LogLevel.disabled: logging.CRITICAL + 1,
        }

        default_level = level_map.get(logging_settings.default_level, logging.INFO)

        root_logger = logging.getLogger()
        root_logger.setLevel(default_level)

        for logger_name in ("vocode", "knowlt"):
            logging.getLogger(logger_name).setLevel(default_level)

        for logger_name, level in logging_settings.enabled_loggers.items():
            override_level = level_map.get(level, default_level)
            logging.getLogger(logger_name).setLevel(override_level)

    @property
    def manager(self) -> BaseManager:
        return self._manager

    @property
    def commands(self) -> CommandManager:
        return self._commands

    @property
    def logs(self) -> list[object]:
        manager = get_log_manager_internal()
        if manager is None:
            return []
        return manager.get_logs()

    async def send_text_message(
        self,
        text: str,
        text_format: manager_proto.TextMessageFormat = manager_proto.TextMessageFormat.PLAIN,
    ) -> None:
        packet = manager_proto.TextMessagePacket(text=text, format=text_format)
        await self.send_packet(packet)

    async def send_packet(self, payload: manager_proto.BasePacket) -> None:
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_packet_id(),
            payload=payload,
        )
        await self._endpoint.send(envelope)

    async def request_text_input(
        self,
        *,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
    ) -> str:
        await self.send_packet(
            manager_proto.InputPromptPacket(title=title, subtitle=subtitle)
        )
        try:
            message = await self._manager.project.input_manager.wait_for_input(
                self._ui_input_channel_id
            )
        except asyncio.CancelledError:
            await self.send_packet(manager_proto.InputPromptPacket())
            raise
        await self.send_packet(manager_proto.InputPromptPacket())
        return message.text

    def start_authentication_session(
        self, provider: str
    ) -> ServerAuthenticationSession:
        session = ServerAuthenticationSession(self, provider)
        self._auth_session = session
        return session

    @property
    def auth_session(self) -> Optional[ServerAuthenticationSession]:
        session = self._auth_session
        if session is None:
            return None
        if not session.is_active:
            return None
        return session

    def enable_branch_packets(self) -> None:
        self._emit_branch_packets = True

    def _build_branch_summaries(
        self,
        result,
    ) -> list[manager_proto.BranchSummary]:
        return [
            manager_proto.BranchSummary(
                branch_id=str(branch.id),
                head_step_id=(
                    str(branch.head_step_id)
                    if branch.head_step_id is not None
                    else None
                ),
                base_step_id=(
                    str(branch.base_step_id)
                    if branch.base_step_id is not None
                    else None
                ),
                label=branch.label,
                created_at=branch.created_at,
                is_active=branch.is_active,
            )
            for branch in result.branch_summaries
        ]

    async def emit_history_mutation(
        self,
        frame: RunnerFrame,
        result,
    ) -> None:
        execution = frame.runner.execution
        if result.removed_step_ids:
            await self.send_packet(
                manager_proto.StepDeletedPacket(
                    step_ids=[str(step_id) for step_id in result.removed_step_ids]
                )
            )
        for upsert_step in result.upserted_steps:
            packet = manager_proto.RunnerReqPacket(
                workflow_id=frame.workflow_name,
                workflow_name=execution.workflow_name,
                workflow_execution_id=str(execution.id),
                step=upsert_step,
                input_required=False,
                display=None,
            )
            await self.send_packet(packet)
        if not self._emit_branch_packets:
            return
        if result.active_branch_id is not None:
            await self.send_packet(
                manager_proto.BranchChangedPacket(
                    workflow_execution_id=str(execution.id),
                    active_branch_id=str(result.active_branch_id),
                    created_branch_id=(
                        str(result.created_branch_id)
                        if result.created_branch_id is not None
                        else None
                    ),
                )
            )
        if result.branch_summaries:
            await self.send_packet(
                manager_proto.BranchListPacket(
                    workflow_execution_id=str(execution.id),
                    branches=self._build_branch_summaries(result),
                )
            )
        await self.send_packet(
            manager_proto.HistoryViewDiffPacket(
                workflow_execution_id=str(execution.id),
                removed_step_ids=[str(step_id) for step_id in result.removed_step_ids],
                upserted_step_ids=[
                    str(step_id) for step_id in result.upserted_step_ids
                ],
            )
        )

    async def _recv_loop(self) -> None:
        while True:
            envelope = await self._endpoint.recv()
            await self.on_ui_packet(envelope)

    async def start(self) -> None:
        if self._started:
            return

        self._apply_logging_settings()

        project = self._manager.project
        settings = project.settings
        enable_know_progress = False
        if settings is not None and settings.know_enabled and settings.know is not None:
            try:
                _ = project.know
            except Exception:
                enable_know_progress = False
            else:
                enable_know_progress = True

        if enable_know_progress:
            progress_id = "know:init"
            await self._emit_progress_start(
                progress_id=progress_id,
                title="Initializing knowledge base",
                message=None,
                mode=manager_proto.ProgressMode.INDETERMINATE,
                bar_type=manager_proto.ProgressBarType.PULSE,
            )
            previous_default_cb = project.know.default_progress_callback
            project.know.default_progress_callback = (
                self._make_knowlt_progress_callback(
                    progress_id=progress_id,
                    title="Indexing repositories",
                )
            )
            try:
                await self._manager.start()
            finally:
                project.know.default_progress_callback = previous_default_cb
                await self._emit_progress_end(progress_id=progress_id)
        else:
            await self._manager.start()
        self._status = manager_proto.UIServerStatus.RUNNING

        self._recv_task = asyncio.create_task(self._recv_loop())

        await workflow_commands.register_workflow_commands(self._commands)
        await self._register_autoapprove_commands()

        settings = self._manager.project.settings
        if settings is not None:
            default_workflow = settings.default_workflow
            if default_workflow is not None and default_workflow in settings.workflows:
                asyncio.create_task(self._manager.start_workflow(default_workflow))

        self._started = True

    async def _emit_progress_start(
        self,
        *,
        progress_id: Optional[str] = None,
        title: Optional[str],
        message: Optional[str],
        mode: manager_proto.ProgressMode,
        bar_type: manager_proto.ProgressBarType,
        on_complete: Optional[manager_proto.ProgressOnComplete] = None,
        complete_message: Optional[str] = None,
    ) -> None:
        resolved_id = progress_id
        if resolved_id is None:
            resolved_id = f"progress:{uuid.uuid4().hex}"
        packet = manager_proto.ProgressPacket(
            progress_id=resolved_id,
            status=manager_proto.ProgressStatus.START,
            title=title,
            message=message,
            mode=mode,
            bar_type=bar_type,
            on_complete=on_complete,
            complete_message=complete_message,
        )
        await self.send_packet(packet)
        self._progress_last_sent_at_by_id.pop(resolved_id, None)

    def _make_knowlt_progress_callback(
        self,
        *,
        progress_id: str,
        title: str,
        message: Optional[str] = None,
        unit: Optional[str] = "files",
    ):
        def _cb(evt) -> None:
            async def _emit() -> None:
                try:
                    processed = float(evt.processed_files)
                except Exception:
                    processed = None
                try:
                    total_files = float(evt.total_files)
                except Exception:
                    total_files = None
                try:
                    elapsed = float(evt.elapsed_seconds)
                except Exception:
                    elapsed = None

                mode = manager_proto.ProgressMode.INDETERMINATE
                bar_type = manager_proto.ProgressBarType.PULSE
                total = None
                if total_files is not None and total_files > 0:
                    total = total_files
                    mode = manager_proto.ProgressMode.DETERMINISTIC
                    bar_type = manager_proto.ProgressBarType.BAR

                try:
                    repo_id = str(evt.repo_id)
                except Exception:
                    repo_id = None

                label = None
                if repo_id:
                    label = self._know_repo_label_by_id.get(repo_id)
                    if not label:
                        try:
                            repo_list = await self._manager.project.know.pm.data.repo.get_by_ids(
                                [repo_id]
                            )
                        except Exception:
                            repo_list = None
                        if repo_list:
                            repo = repo_list[0]
                            root = repo.root_path or ""
                            if root:
                                label = f"{repo.name} ({root})"
                            else:
                                label = repo.name
                            self._know_repo_label_by_id[repo_id] = label

                resolved_message = message
                if resolved_message is None:
                    if label:
                        resolved_message = label
                    elif repo_id is not None:
                        resolved_message = repo_id

                await self.emit_progress_update(
                    progress_id=progress_id,
                    title=title,
                    message=resolved_message,
                    mode=mode,
                    bar_type=bar_type,
                    completed=processed,
                    total=total,
                    unit=unit,
                    done=(
                        True
                        if (
                            processed is not None
                            and total is not None
                            and processed >= total
                        )
                        else None
                    ),
                )

            asyncio.create_task(_emit())

        return _cb

    async def refresh_know_repo_with_progress(self, repo) -> None:
        progress_id = f"know:scan:{repo.name}"
        await self._emit_progress_start(
            progress_id=progress_id,
            title="Indexing repository",
            message=repo.name,
            mode=manager_proto.ProgressMode.INDETERMINATE,
            bar_type=manager_proto.ProgressBarType.PULSE,
            on_complete=manager_proto.ProgressOnComplete.HIDE,
        )
        cb = self._make_knowlt_progress_callback(
            progress_id=progress_id,
            title="Indexing repository",
            message=repo.name,
        )
        try:
            await self._manager.project.know.refresh(repo, progress_callback=cb)
        finally:
            await self._emit_progress_end(progress_id=progress_id)

    async def refresh_know_all_with_progress(self) -> None:
        progress_id = "know:refresh_all"
        await self._emit_progress_start(
            progress_id=progress_id,
            title="Indexing repositories",
            message=None,
            mode=manager_proto.ProgressMode.INDETERMINATE,
            bar_type=manager_proto.ProgressBarType.PULSE,
            on_complete=manager_proto.ProgressOnComplete.HIDE,
        )
        cb = self._make_knowlt_progress_callback(
            progress_id=progress_id,
            title="Indexing repositories",
            message=None,
        )
        try:
            await self._manager.project.know.refresh_all(progress_callback=cb)
        finally:
            await self._emit_progress_end(progress_id=progress_id)

    async def _emit_progress_end(
        self,
        *,
        progress_id: str,
        on_complete: Optional[manager_proto.ProgressOnComplete] = None,
        complete_message: Optional[str] = None,
    ) -> None:
        packet = manager_proto.ProgressPacket(
            progress_id=progress_id,
            status=manager_proto.ProgressStatus.END,
            done=True,
            on_complete=on_complete,
            complete_message=complete_message,
        )
        await self.send_packet(packet)

    async def emit_progress_update(
        self,
        *,
        progress_id: str,
        title: Optional[str] = None,
        message: Optional[str] = None,
        mode: Optional[manager_proto.ProgressMode] = None,
        bar_type: Optional[manager_proto.ProgressBarType] = None,
        completed: Optional[float] = None,
        total: Optional[float] = None,
        unit: Optional[str] = None,
        done: Optional[bool] = None,
        on_complete: Optional[manager_proto.ProgressOnComplete] = None,
        complete_message: Optional[str] = None,
        min_interval_s: float = 0.25,
    ) -> None:
        now = time.monotonic()
        last = self._progress_last_sent_at_by_id.get(progress_id)
        if last is not None and (now - last) < min_interval_s:
            return
        self._progress_last_sent_at_by_id[progress_id] = now

        packet = manager_proto.ProgressPacket(
            progress_id=progress_id,
            status=manager_proto.ProgressStatus.UPDATE,
            title=title,
            message=message,
            mode=mode if mode is not None else manager_proto.ProgressMode.DETERMINISTIC,
            bar_type=(
                bar_type if bar_type is not None else manager_proto.ProgressBarType.BAR
            ),
            completed=completed,
            total=total,
            unit=unit,
            done=done,
            on_complete=on_complete,
            complete_message=complete_message,
        )
        await self.send_packet(packet)

    async def _register_autoapprove_commands(self) -> None:
        async def aa(server: "UIServer", args: list[str]) -> None:
            if args:
                raise workflow_commands.CommandError("Usage: /aa")
            step = server._last_tool_confirmation_step
            if step is None or step.type != state.StepType.TOOL_REQUEST:
                raise workflow_commands.CommandError(
                    "No tool confirmation is currently pending."
                )
            message = step.message
            if message is None:
                raise workflow_commands.CommandError(
                    "No tool confirmation is currently pending."
                )

            added: list[str] = []
            for tool_req in message.tool_call_requests:
                if tool_req.status != state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
                    continue
                server.manager.project.project_state.autoapprove.add_tool(tool_req.name)
                added.append(tool_req.name)

            runner = server.manager.current_runner
            if runner is None:
                raise workflow_commands.CommandError(
                    "No tool confirmation is currently pending."
                )
            if server._last_tool_confirmation_runner_id != runner.input_workflow_id:
                raise workflow_commands.CommandError(
                    "No tool confirmation is currently pending."
                )
            accepted = await server.manager.project.input_manager.publish(
                runner.input_workflow_id,
                state.Message(role=models.Role.USER, text=""),
                queue_if_unhandled=False,
            )
            if not accepted:
                raise workflow_commands.CommandError(
                    "No tool confirmation is currently pending."
                )
            prompt_packet = manager_proto.InputPromptPacket()
            await server.send_packet(prompt_packet)
            server._last_tool_confirmation_step = None
            server._last_tool_confirmation_runner_id = None

            if added:
                unique = sorted(set(added))
                await server.send_text_message(
                    "Session auto-approve enabled for: " + ", ".join(unique)
                )

        await self._commands.register(
            "aa",
            aa,
            description="Auto-approve similar tool calls for this session",
            params=[],
            hidden=True,
        )

        async def autoapprove(server: "UIServer", args: list[str]) -> None:
            if not args:
                raise workflow_commands.CommandError(
                    "Usage: /autoapprove <list|add|remove|clear>"
                )
            sub = args[0]
            rest = args[1:]
            st = server.manager.project.project_state.autoapprove

            if sub == "list":
                if rest:
                    raise workflow_commands.CommandError("Usage: /autoapprove list")
                if not st.policies_by_tool:
                    await server.send_text_message("No session auto-approve rules.")
                    return
                lines: list[str] = ["Session auto-approve rules:"]
                for name in sorted(st.policies_by_tool.keys()):
                    policy = st.policies_by_tool[name]
                    if policy.approve_all:
                        lines.append(f"  - {name}: approve_all")
                    elif not policy.rules:
                        lines.append(f"  - {name}: (no rules)")
                    else:
                        lines.append(f"  - {name}:")
                        for rule in policy.rules:
                            lines.append(f"      - {rule.key} ~= {rule.pattern}")
                await server.send_text_message("\n".join(lines))
                return

            if sub == "add":
                if len(rest) not in (1, 3):
                    raise workflow_commands.CommandError(
                        "Usage: /autoapprove add <tool> [<dotted_key> <pattern>]"
                    )
                tool_name = rest[0]
                if len(rest) == 1:
                    st.add_tool(tool_name, approve_all=True)
                    await server.send_text_message(
                        f"Session auto-approve enabled for tool: {tool_name}"
                    )
                    return
                st.add_rule(tool_name, key=rest[1], pattern=rest[2])
                await server.send_text_message(
                    f"Session auto-approve rule added for tool: {tool_name}"
                )
                return

            if sub == "remove":
                if len(rest) != 1:
                    raise workflow_commands.CommandError(
                        "Usage: /autoapprove remove <tool>"
                    )
                tool_name = rest[0]
                removed = st.remove_tool(tool_name)
                if removed:
                    await server.send_text_message(
                        f"Session auto-approve disabled for tool: {tool_name}"
                    )
                else:
                    await server.send_text_message(
                        f"No session auto-approve rule found for tool: {tool_name}"
                    )
                return

            if sub == "clear":
                if rest:
                    raise workflow_commands.CommandError("Usage: /autoapprove clear")
                st.clear()
                await server.send_text_message("Session auto-approve rules cleared.")
                return

            raise workflow_commands.CommandError(
                "Usage: /autoapprove <list|add|remove|clear>"
            )

        await self._commands.register(
            "autoapprove",
            autoapprove,
            description="Manage session auto-approve rules",
            params=["<list|add|remove|clear>", "..."],
        )

    async def stop(self) -> None:
        if not self._started:
            return

        self._status = manager_proto.UIServerStatus.IDLE

        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        self._rpc.cancel_all()
        await self._manager.project.input_manager.reset_workflow(
            self._ui_input_channel_id
        )

        await self._manager.stop()
        self._started = False

    # Runner event handling
    async def _handle_runner_step_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        execution = event.execution
        step = event.step
        if step is None:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP,
                message=None,
            )

        message = step.message

        display: Optional[manager_proto.RunnerReqDisplayOpts] = None
        node_name = step.execution.node
        node_by_name = frame.runner.workflow.graph.node_by_name
        node = node_by_name.get(node_name)
        if node is not None and (
            node.collapse is not None
            or node.collapse_lines is not None
            or not node.visible
            or node.tool_collapse is not None
        ):
            display = manager_proto.RunnerReqDisplayOpts(
                collapse=node.collapse,
                collapse_lines=node.collapse_lines,
                visible=node.visible,
                tool_collapse=node.tool_collapse,
            )

        input_title: Optional[str] = None
        input_subtitle: Optional[str] = None

        needs_confirmation = False
        if step.type == state.StepType.TOOL_REQUEST and message is not None:
            for tool_req in message.tool_call_requests:
                if tool_req.status == state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
                    needs_confirmation = True
                    break

        input_required = False
        if step.type in (state.StepType.PROMPT, state.StepType.PROMPT_CONFIRM):
            input_required = True
            if step.type == state.StepType.PROMPT_CONFIRM:
                input_title = "Press enter to confirm or provide a reply"
            else:
                input_title = "Input"
        elif step.type == state.StepType.TOOL_REQUEST and needs_confirmation:
            input_required = True
            input_title = "Please confirm the tool call"
            input_subtitle = (
                "Empty line confirms, any text to reject with a message. "
                "Tip: type /aa to auto-approve similar calls for this session"
            )
            self._last_tool_confirmation_step = step
            self._last_tool_confirmation_runner_id = frame.runner.input_workflow_id

        packet = manager_proto.RunnerReqPacket(
            workflow_id=frame.workflow_name,
            workflow_name=execution.workflow_name,
            workflow_execution_id=str(execution.id),
            step=step,
            input_required=input_required,
            display=display,
        )
        await self.send_packet(packet)

        if not input_required:
            if step.type == state.StepType.TOOL_REQUEST and not needs_confirmation:
                return runner_proto.RunEventResp(
                    resp_type=runner_proto.RunEventResponseType.APPROVE,
                    message=None,
                )

            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP,
                message=None,
            )

        prompt_packet = manager_proto.InputPromptPacket(
            title=input_title,
            subtitle=input_subtitle,
        )
        await self.send_packet(prompt_packet)

        return runner_proto.RunEventResp(
            resp_type=runner_proto.RunEventResponseType.NOOP,
            message=None,
        )

    async def _handle_runner_status_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        # If we stopped running, cancel all input waiters
        stats = event.stats
        if stats is not None and stats.status in (
            state.RunnerStatus.STOPPED,
            state.RunnerStatus.FINISHED,
        ):
            self._last_tool_confirmation_step = None
            self._last_tool_confirmation_runner_id = None
            prompt_packet = manager_proto.InputPromptPacket()
            await self.send_packet(prompt_packet)

        # Generate runner stack summary
        runners: list[manager_proto.RunnerStackFrame] = []
        active_node_started_at = None
        last_user_input_at = None
        active_workflow_usage: Optional[state.LLMUsageStats] = None
        last_step_usage: Optional[state.LLMUsageStats] = None
        for runner_frame in self._manager.runner_stack:
            stats = runner_frame.last_stats
            if stats is None:
                continue
            execution = runner_frame.runner.execution
            node_name = ""
            node_execution_id = None
            node_started_at = None
            stats_execution_id = stats.current_node_execution_id
            if stats_execution_id is not None:
                node_execution = execution.node_executions.get(stats_execution_id)
                if node_execution is not None:
                    if node_execution.step_ids:
                        first_step = execution.get_step(node_execution.step_ids[0])
                        node_started_at = first_step.created_at
                    node_name = node_execution.node
                    node_execution_id = str(node_execution.id)
            runners.append(
                manager_proto.RunnerStackFrame(
                    workflow_name=execution.workflow_name,
                    workflow_execution_id=str(execution.id),
                    node_name=node_name,
                    node_execution_id=node_execution_id,
                    status=stats.status,
                )
            )
            if node_started_at is not None:
                active_node_started_at = node_started_at
            if execution.last_user_input_at is not None:
                last_user_input_at = execution.last_user_input_at
            if execution.llm_usage is not None:
                active_workflow_usage = execution.llm_usage
            if execution.last_step_llm_usage is not None:
                last_step_usage = execution.last_step_llm_usage

        # Send stack packet
        state_packet = manager_proto.UIServerStatePacket(
            status=self._status,
            runners=runners,
            active_node_started_at=active_node_started_at,
            last_user_input_at=last_user_input_at,
            active_workflow_llm_usage=active_workflow_usage,
            last_step_llm_usage=last_step_usage,
            project_llm_usage=self._manager.project.llm_usage,
        )
        await self.send_packet(state_packet)
        return runner_proto.RunEventResp(
            resp_type=runner_proto.RunEventResponseType.NOOP,
            message=None,
        )

    async def _handle_runner_start_workflow_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ):
        payload = event.start_workflow
        if payload is None:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.MESSAGE,
                message=state.Message(
                    role=models.Role.SYSTEM,
                    text="Start-workflow event is missing payload.",
                ),
            )
        try:
            await self._manager.start_workflow(
                payload.workflow_name,
                initial_message=payload.initial_message,
            )
        except Exception as ex:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.MESSAGE,
                message=state.Message(
                    role=models.Role.SYSTEM,
                    text=f"Failed to start workflow '{payload.workflow_name}': {ex}",
                ),
            )

        # We don't expect anything to be passed to next iteration
        return None

    async def on_runner_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        if event.kind == runner_proto.RunEventReqKind.STATUS:
            return await self._handle_runner_status_event(frame, event)

        if event.kind == runner_proto.RunEventReqKind.START_WORKFLOW:
            return await self._handle_runner_start_workflow_event(frame, event)

        return await self._handle_runner_step_event(frame, event)

    # UI packets handling
    async def _on_user_input_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.USER_INPUT:
            return None

        message = payload.message
        text = message.text

        if text.startswith("/") and len(text) > 1:
            handled = await self._commands.execute(self, text[1:])
            if not handled:
                await self.send_text_message(
                    f"Unknown command: /{text[1:].split(maxsplit=1)[0]}"
                )
            return None

        runner = self._manager.current_runner
        if runner is not None and runner.status != state.RunnerStatus.STOPPED:
            accepted = await self._manager.project.input_manager.publish(
                runner.input_workflow_id,
                message,
                queue_if_unhandled=False,
            )
            if accepted:
                prompt_packet = manager_proto.InputPromptPacket()
                await self.send_packet(prompt_packet)
                return None

        accepted = await self._manager.project.input_manager.publish(
            self._ui_input_channel_id,
            message,
            queue_if_unhandled=False,
        )
        if accepted:
            prompt_packet = manager_proto.InputPromptPacket()
            await self.send_packet(prompt_packet)
            return None

        if runner is not None and runner.status == state.RunnerStatus.STOPPED:
            res = await self._manager.edit_history_with_text(
                text,
                resume=False,
            )
            if res.changed:
                frame = self._manager.runner_stack[-1]
                await self.emit_history_mutation(frame, res)
            if res.changed:
                await self._manager.continue_current_runner()
            if not res.changed:
                await self.send_text_message(
                    "Unable to edit history: no previous user input to replace."
                )
            return None

        await self.send_text_message("Input was rejected: no active input request.")

        return None

    async def _on_autocomplete_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.AUTOCOMPLETE_REQ:
            return None
        req = cast(manager_proto.AutocompleteReqPacket, payload)
        items = await self._autocomplete.get_completions(
            self,
            req.text,
            req.row,
            req.col,
        )
        resp_items = [
            manager_proto.AutocompleteItem(
                title=item.title,
                replace_start=item.replace_start,
                replace_text=item.replace_text,
                insert_text=item.insert_text,
            )
            for item in items
        ]
        resp = manager_proto.AutocompleteRespPacket(items=resp_items)
        await self.send_packet(resp)
        return None

    async def _on_stop_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.STOP_REQ:
            return None
        await self._manager.stop_current_runner()
        return None

    async def _on_log_req_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.LOG_REQ:
            return None
        req = cast(manager_proto.LogReqPacket, payload)
        manager = get_log_manager_internal()
        if manager is None:
            return manager_proto.LogRespPacket(offset=req.offset, total=0, entries=[])
        records = manager.get_logs()
        total = len(records)
        if req.offset < 0:
            offset = 0
        else:
            offset = req.offset
        if offset > total:
            offset = total
        limit = req.limit
        if limit is None:
            end = total
        else:
            if limit < 0:
                limit = 0
            end = offset + limit
        if end > total:
            end = total
        entries: list[manager_proto.LogEntry] = []
        for index in range(offset, end):
            record = records[index]
            level = manager_proto.LogLevel.INFO
            if record.level <= logging.DEBUG:
                level = manager_proto.LogLevel.DEBUG
            elif record.level <= logging.INFO:
                level = manager_proto.LogLevel.INFO
            elif record.level <= logging.WARNING:
                level = manager_proto.LogLevel.WARNING
            elif record.level <= logging.ERROR:
                level = manager_proto.LogLevel.ERROR
            else:
                level = manager_proto.LogLevel.CRITICAL
            entry = manager_proto.LogEntry(
                index=index,
                logger_name=record.logger_name,
                level=level,
                level_name=record.level_name,
                message=record.message,
                created=record.created,
            )
            entries.append(entry)
        return manager_proto.LogRespPacket(
            offset=offset,
            total=total,
            entries=entries,
        )

    async def on_ui_packet(self, envelope: manager_proto.BasePacketEnvelope) -> bool:
        logger.debug("UIServer.on_ui_packet", pack=envelope)
        return await self._router.handle(envelope)
