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

        self._router.register(
            manager_proto.BasePacketKind.USER_INPUT,
            self._on_user_input_packet,
        )

    def _next_packet_id(self) -> int:
        self._push_msg_id += 1
        return self._push_msg_id

    @property
    def manager(self) -> BaseManager:
        return self._manager

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

        packet = manager_proto.RunnerReqPacket(
            workflow_id=frame.workflow_name,
            workflow_name=execution.workflow_name,
            workflow_execution_id=str(execution.id),
            step=step,
        )
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_packet_id(),
            payload=packet,
        )
        await self._endpoint.send(envelope)

        if step.type != state.StepType.PROMPT:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP,
                message=None,
            )

        waiter = self._push_input_waiter()
        resp_packet = await waiter

        resp_type = runner_proto.RunEventResponseType.APPROVE
        if resp_packet.message is not None:
            resp_type = runner_proto.RunEventResponseType.MESSAGE

        return runner_proto.RunEventResp(
            resp_type=resp_type,
            message=resp_packet.message,
        )

    async def _handle_runner_status_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
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

        state_packet = manager_proto.UIServerStatePacket(
            status=self._status,
            runners=runners,
        )
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_packet_id(),
            payload=state_packet,
        )
        await self._endpoint.send(envelope)
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

        waiter = self._pop_input_waiter()
        if waiter is None:
            return None

        if not waiter.done():
            user_input = cast(manager_proto.UserInputPacket, payload)
            waiter.set_result(user_input)

        return None

    async def on_ui_packet(self, envelope: manager_proto.BasePacketEnvelope) -> bool:
        logger.debug("UIServer.on_ui_packet", pack=envelope)
        return await self._router.handle(envelope)
