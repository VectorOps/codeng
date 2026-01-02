from __future__ import annotations

from typing import Optional, cast

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
        self._status = manager_proto.UIServerStatus.RUNNING
        self._push_msg_id = 0
    def _next_packet_id(self) -> int:
        self._push_msg_id += 1
        return self._push_msg_id

    @property
    def manager(self) -> BaseManager:
        return self._manager

    # Runner event handling
    async def _handle_runner_step_event(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        execution = event.execution
        packet = manager_proto.RunnerReqPacket(
            workflow_id=frame.workflow_name,
            workflow_name=execution.workflow_name,
            workflow_execution_id=str(execution.id),
            step=event.step,
        )

        response = await self._rpc.call(packet)
        if response is None:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP
            )

        if response.kind != manager_proto.BasePacketKind.RUNNER_RESP:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP
            )

        resp_packet = cast(manager_proto.RunnerRespPacket, response)
        return runner_proto.RunEventResp(
            resp_type=resp_packet.resp_type,
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
    async def on_ui_packet(self, envelope: manager_proto.BasePacketEnvelope) -> bool:
        return await self._router.handle(envelope)
