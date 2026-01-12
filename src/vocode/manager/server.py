from __future__ import annotations

import asyncio
from typing import Optional, cast

from vocode import state
from vocode.logger import logger
from vocode.project import Project
from vocode.runner import proto as runner_proto

from .base import BaseManager, RunnerFrame
from .helpers import BaseEndpoint, IncomingPacketRouter, RpcHelper
from . import proto as manager_proto
from .autocomplete import AutocompleteManager
from .commands import CommandManager
from .commands import workflows as workflow_commands


class UIServer:
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
        self._input_waiters: list[asyncio.Future[manager_proto.UserInputPacket]] = []
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._started = False
        self._autocomplete = AutocompleteManager()
        self._commands = CommandManager()

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

    def _next_packet_id(self) -> int:
        self._push_msg_id += 1
        return self._push_msg_id

    @property
    def manager(self) -> BaseManager:
        return self._manager

    @property
    def commands(self) -> CommandManager:
        return self._commands

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

    async def _recv_loop(self) -> None:
        while True:
            envelope = await self._endpoint.recv()
            await self.on_ui_packet(envelope)

    async def start(self) -> None:
        if self._started:
            return

        await self._manager.start()
        self._status = manager_proto.UIServerStatus.RUNNING

        self._recv_task = asyncio.create_task(self._recv_loop())

        await workflow_commands.register_workflow_commands(self._commands)

        settings = self._manager.project.settings
        if settings is not None:
            default_workflow = settings.default_workflow
            if default_workflow is not None and default_workflow in settings.workflows:
                asyncio.create_task(self._manager.start_workflow(default_workflow))

        self._started = True

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

        for waiter in self._input_waiters:
            if not waiter.done():
                waiter.cancel()
        self._input_waiters.clear()

        await self._manager.stop()
        self._started = False

    # Input waiters
    def _push_input_waiter(
        self,
    ) -> asyncio.Future[manager_proto.UserInputPacket]:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[manager_proto.UserInputPacket] = loop.create_future()
        self._input_waiters.append(waiter)
        return waiter

    def _pop_input_waiter(
        self,
    ) -> Optional[asyncio.Future[manager_proto.UserInputPacket]]:
        if not self._input_waiters:
            return None
        return self._input_waiters.pop()

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
            input_subtitle = "Empty line confirms, any text to reject with a message"

        packet = manager_proto.RunnerReqPacket(
            workflow_id=frame.workflow_name,
            workflow_name=execution.workflow_name,
            workflow_execution_id=str(execution.id),
            step=step,
            input_required=input_required,
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

        waiter = self._push_input_waiter()
        resp_packet = await waiter
        message_packet = resp_packet.message

        if step.type == state.StepType.PROMPT:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.MESSAGE,
                message=message_packet,
            )

        if step.type == state.StepType.PROMPT_CONFIRM:
            text = message_packet.text if message_packet is not None else ""
            if text:
                return runner_proto.RunEventResp(
                    resp_type=runner_proto.RunEventResponseType.MESSAGE,
                    message=message_packet,
                )

            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.APPROVE,
                message=None,
            )

        if step.type == state.StepType.TOOL_REQUEST:
            resp_type = runner_proto.RunEventResponseType.APPROVE
            if message_packet is not None and message_packet.text:
                resp_type = runner_proto.RunEventResponseType.DECLINE

            return runner_proto.RunEventResp(
                resp_type=resp_type,
                message=message_packet,
            )

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
            for waiter in self._input_waiters:
                if not waiter.done():
                    waiter.cancel()
            self._input_waiters.clear()
            prompt_packet = manager_proto.InputPromptPacket()
            await self.send_packet(prompt_packet)

        # Generate runner stack summary
        runners: list[manager_proto.RunnerStackFrame] = []
        for runner_frame in self._manager.runner_stack:
            stats = runner_frame.last_stats
            if stats is None:
                continue
            execution = runner_frame.runner.execution
            node_name = stats.current_node_name or ""
            runners.append(
                manager_proto.RunnerStackFrame(
                    workflow_name=execution.workflow_name,
                    workflow_execution_id=str(execution.id),
                    node_name=node_name,
                    status=stats.status,
                )
            )

        # Send stack packet
        state_packet = manager_proto.UIServerStatePacket(
            status=self._status,
            runners=runners,
        )
        await self.send_packet(state_packet)
        return runner_proto.RunEventResp(
            resp_type=runner_proto.RunEventResponseType.NOOP,
            message=None,
        )

    async def on_runner_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        if event.kind == runner_proto.RunEventReqKind.STATUS:
            return await self._handle_runner_status_event(frame, event)
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

        waiter = self._pop_input_waiter()
        if waiter is None:
            # TODO: No waiter needs input, send error message
            return None

        if not waiter.done():
            user_input = cast(manager_proto.UserInputPacket, payload)
            waiter.set_result(user_input)
            prompt_packet = manager_proto.InputPromptPacket()
            await self.send_packet(prompt_packet)

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
            manager_proto.AutocompleteItem(title=item.title, value=item.value)
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

    async def on_ui_packet(self, envelope: manager_proto.BasePacketEnvelope) -> bool:
        logger.debug("UIServer.on_ui_packet", pack=envelope)
        return await self._router.handle(envelope)
