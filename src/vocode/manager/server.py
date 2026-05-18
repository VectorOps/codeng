from __future__ import annotations

import logging
import asyncio
from typing import Optional

import vocode.error_reporting as error_reporting
from vocode import input_manager
from vocode import settings as vocode_settings
from vocode import models
from vocode import state
from vocode.logger import get_log_manager_internal, init_log_manager, logger
from vocode.project import Project
from vocode.runner import proto as runner_proto
from vocode.auth import ServerAuthenticationSession
from .mcp_session import ServerMCPAuthenticationSession

from .base import BaseManager, RunnerFrame
from .history_packets import HistoryMutationPacketEmitter
from .interfaces import UIManager
from .know_progress_bridge import KnowProgressBridge
from .message_controller import UIMessageController
from .progress_emitter import ProgressEmitter
from .runner_event_controller import RunnerEventController
from .ui_event_bridge import ProjectUIEventBridge
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
        self._auth_session: Optional[ServerAuthenticationSession] = None
        self._mcp_auth_session: Optional[ServerMCPAuthenticationSession] = None
        self._history_packet_emitter = HistoryMutationPacketEmitter(self)
        self._progress_emitter = ProgressEmitter(self)
        self._know_progress_bridge = KnowProgressBridge(
            project=self._manager.project,
            progress_emitter=self._progress_emitter,
        )
        self._message_controller = UIMessageController(
            manager=self._manager,
            commands=self._commands,
            autocomplete=self._autocomplete,
            packet_sender=self,
            emit_history_mutation=self.emit_history_mutation,
        )
        self._ui_event_bridge = ProjectUIEventBridge(
            project=self._manager.project,
            packet_sender=self,
        )
        self._runner_event_controller = RunnerEventController(
            manager=self._manager,
            packet_sender=self,
            publish_workflow_start_error=self._publish_workflow_start_error,
            status=self._status,
        )

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

    def _set_status(self, status: manager_proto.UIServerStatus) -> None:
        self._status = status
        self._runner_event_controller.status = status

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
    def manager(self) -> UIManager:
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
                input_type=input_manager.INPUT_TYPE_INTERACTIVE,
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

    def start_mcp_authentication_session(
        self,
        operation,
    ) -> ServerMCPAuthenticationSession:
        session = ServerMCPAuthenticationSession(operation)
        self._mcp_auth_session = session
        return session

    def clear_mcp_authentication_session(self) -> None:
        self._mcp_auth_session = None

    @property
    def auth_session(self) -> Optional[ServerAuthenticationSession]:
        session = self._auth_session
        if session is None:
            return None
        if not session.is_active:
            return None
        return session

    @property
    def mcp_auth_session(self) -> Optional[ServerMCPAuthenticationSession]:
        session = self._mcp_auth_session
        if session is None:
            return None
        if not session.is_active:
            return None
        return session

    def enable_branch_packets(self) -> None:
        self._history_packet_emitter.emit_branch_packets = True

    async def emit_history_mutation(
        self,
        frame: RunnerFrame,
        result,
    ) -> None:
        await self._history_packet_emitter.emit(frame, result)

    async def _recv_loop(self) -> None:
        while True:
            envelope = await self._endpoint.recv()
            await self.on_ui_packet(envelope)

    async def _publish_workflow_start_error(
        self,
        workflow_name: str,
        error: Exception,
    ) -> None:
        workflow_error = error_reporting.build_workflow_validation_error(
            workflow_name,
            error,
        )
        await self._manager.project.publish_ui_event(
            error_reporting.build_workflow_validation_ui_event(workflow_error)
        )

    async def _autostart_default_workflow(self, workflow_name: str) -> None:
        try:
            await self._manager.start_workflow(workflow_name)
        except Exception as exc:
            await self._publish_workflow_start_error(workflow_name, exc)

    async def start(self) -> None:
        if self._started:
            return

        self._apply_logging_settings()

        project = self._manager.project
        self._ui_event_bridge.start()

        settings = project.settings
        enable_know_progress = False
        try:
            if (
                settings is not None
                and settings.know_enabled
                and settings.know is not None
            ):
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
                    self._know_progress_bridge.make_progress_callback(
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
        except Exception:
            self._ui_event_bridge.stop()
            raise
        self._set_status(manager_proto.UIServerStatus.RUNNING)

        self._recv_task = asyncio.create_task(self._recv_loop())

        await workflow_commands.register_workflow_commands(self._commands)

        settings = self._manager.project.settings
        if settings is not None:
            default_workflow = settings.default_workflow
            if default_workflow is not None and default_workflow in settings.workflows:
                asyncio.create_task(self._autostart_default_workflow(default_workflow))

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
        await self._progress_emitter.emit_start(
            progress_id=progress_id,
            title=title,
            message=message,
            mode=mode,
            bar_type=bar_type,
            on_complete=on_complete,
            complete_message=complete_message,
        )

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
        cb = self._know_progress_bridge.make_progress_callback(
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
        cb = self._know_progress_bridge.make_progress_callback(
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
        await self._progress_emitter.emit_end(
            progress_id=progress_id,
            on_complete=on_complete,
            complete_message=complete_message,
        )

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
        await self._progress_emitter.emit_update(
            progress_id=progress_id,
            title=title,
            message=message,
            mode=mode,
            bar_type=bar_type,
            completed=completed,
            total=total,
            unit=unit,
            done=done,
            on_complete=on_complete,
            complete_message=complete_message,
            min_interval_s=min_interval_s,
        )

    async def stop(self) -> None:
        if not self._started:
            return

        self._set_status(manager_proto.UIServerStatus.IDLE)

        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        self._rpc.cancel_all()
        await self._manager.project.input_manager.reset()

        self._ui_event_bridge.stop()

        await self._manager.stop()
        self._started = False

    # Runner event handling
    async def on_runner_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        return await self._runner_event_controller.handle(frame, event)

    # UI packets handling
    async def _on_user_input_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        return await self._message_controller.handle(self, envelope)

    async def _on_autocomplete_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        return await self._message_controller.handle(self, envelope)

    async def _on_stop_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        return await self._message_controller.handle(self, envelope)

    async def _on_log_req_packet(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        return await self._message_controller.handle(self, envelope)

    async def on_ui_packet(self, envelope: manager_proto.BasePacketEnvelope) -> bool:
        logger.debug("UIServer.on_ui_packet", pack=envelope)
        return await self._router.handle(envelope)
