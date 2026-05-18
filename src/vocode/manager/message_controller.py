from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Optional, cast

from vocode import input_manager, state
from vocode.history.models import HistoryMutationResult
from vocode.logger import get_log_manager_internal

from . import proto as manager_proto
from .autocomplete import AutocompleteManager
from .base import RunnerFrame
from .commands import CommandManager
from .interfaces import UIManager, UIPacketSender


class UIMessageController:
    def __init__(
        self,
        *,
        manager: UIManager,
        commands: CommandManager,
        autocomplete: AutocompleteManager,
        packet_sender: UIPacketSender,
        emit_history_mutation: Callable[
            [RunnerFrame, HistoryMutationResult], Awaitable[None]
        ],
    ) -> None:
        self._manager = manager
        self._commands = commands
        self._autocomplete = autocomplete
        self._packet_sender = packet_sender
        self._emit_history_mutation = emit_history_mutation

    async def handle(
        self,
        server,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        kind = payload.kind
        if kind == manager_proto.BasePacketKind.USER_INPUT:
            return await self._handle_user_input(server, envelope)
        if kind == manager_proto.BasePacketKind.AUTOCOMPLETE_REQ:
            return await self._handle_autocomplete(server, envelope)
        if kind == manager_proto.BasePacketKind.STOP_REQ:
            return await self._handle_stop(envelope)
        if kind == manager_proto.BasePacketKind.LOG_REQ:
            return await self._handle_log_request(envelope)
        return None

    async def _handle_user_input(
        self,
        server,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.USER_INPUT:
            return None

        message = payload.message
        text = message.text

        if text.startswith("/") and len(text) > 1:
            handled = await self._commands.execute(server, text[1:])
            if not handled:
                await self._packet_sender.send_text_message(
                    f"Unknown command: /{text[1:].split(maxsplit=1)[0]}"
                )
            return None

        runner = self._manager.current_runner
        if runner is not None and runner.status != state.RunnerStatus.STOPPED:
            accepted = await self._manager.project.input_manager.publish(
                message,
                queue=False,
                input_type=input_manager.INPUT_TYPE_INTERACTIVE,
            )
            if accepted:
                await self._packet_sender.send_packet(manager_proto.InputPromptPacket())
                return None

        accepted = await self._manager.project.input_manager.publish(
            message,
            queue=False,
            input_type=input_manager.INPUT_TYPE_INTERACTIVE,
        )
        if accepted:
            await self._packet_sender.send_packet(manager_proto.InputPromptPacket())
            return None

        if runner is not None and runner.status == state.RunnerStatus.STOPPED:
            result = await self._manager.edit_history_with_text(
                text,
                resume=False,
            )
            if result.changed:
                frame = self._manager.runner_stack[-1]
                await self._emit_history_mutation(frame, result)
                await self._manager.continue_current_runner()
            else:
                await self._packet_sender.send_text_message(
                    "Unable to edit history: no previous user input to replace."
                )
            return None

        await self._packet_sender.send_text_message(
            "Input was rejected: no active input request."
        )
        return None

    async def _handle_autocomplete(
        self,
        server,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.AUTOCOMPLETE_REQ:
            return None
        req = cast(manager_proto.AutocompleteReqPacket, payload)
        items = await self._autocomplete.get_completions(
            server,
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
        await self._packet_sender.send_packet(
            manager_proto.AutocompleteRespPacket(items=resp_items)
        )
        return None

    async def _handle_stop(
        self,
        envelope: manager_proto.BasePacketEnvelope,
    ) -> Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if payload.kind != manager_proto.BasePacketKind.STOP_REQ:
            return None
        await self._manager.stop_current_runner()
        return None

    async def _handle_log_request(
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
            entries.append(
                manager_proto.LogEntry(
                    index=index,
                    logger_name=record.logger_name,
                    level=level,
                    level_name=record.level_name,
                    message=record.message,
                    created=record.created,
                )
            )
        return manager_proto.LogRespPacket(
            offset=offset,
            total=total,
            entries=entries,
        )
