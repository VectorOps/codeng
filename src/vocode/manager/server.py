from __future__ import annotations

from typing import Optional, cast

from vocode.project import Project
from vocode.runner.proto import RunEventReq, RunEventResp, RunEventResponseType

from .base import BaseManager, RunnerFrame
from .helpers import BaseEndpoint, IncomingPacketRouter, RpcHelper
from .proto import BasePacketEnvelope, BasePacketKind, RunnerReqPacket, RunnerRespPacket


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

    @property
    def manager(self) -> BaseManager:
        return self._manager

    async def on_runner_event(
        self,
        frame: RunnerFrame,
        event: RunEventReq,
    ) -> Optional[RunEventResp]:
        execution = event.execution
        packet = RunnerReqPacket(
            workflow_id=frame.workflow_name,
            workflow_name=execution.workflow_name,
            workflow_execution_id=str(execution.id),
            step=event.step,
        )

        response = await self._rpc.call(packet)
        if response is None:
            return RunEventResp(resp_type=RunEventResponseType.NOOP)

        if response.kind != BasePacketKind.RUNNER_RESP:
            return RunEventResp(resp_type=RunEventResponseType.NOOP)

        resp_packet = cast(RunnerRespPacket, response)
        return RunEventResp(
            resp_type=resp_packet.resp_type,
            message=resp_packet.message,
        )

    async def on_ui_packet(self, envelope: BasePacketEnvelope) -> bool:
        return await self._router.handle(envelope)
