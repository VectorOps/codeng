from __future__ import annotations

from enum import Enum
from typing import Annotated, Optional
import typing

from pydantic import BaseModel, Field
from vocode import state
from vocode.runner import proto as runner_proto


class BasePacketKind(str, Enum):
    ACK = "ack"
    RUNNER_REQ = "runner_req"
    RUNNER_RESP = "runner_resp"
    UI_STATE = "ui_state"


class AckPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.ACK] = Field(default=BasePacketKind.ACK)


class RunnerReqPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.RUNNER_REQ] = Field(
        default=BasePacketKind.RUNNER_REQ
    )
    workflow_id: str
    workflow_name: str
    workflow_execution_id: str
    step: state.Step


class RunnerRespPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.RUNNER_RESP] = Field(
        default=BasePacketKind.RUNNER_RESP
    )
    resp_type: runner_proto.RunEventResponseType
    message: Optional[state.Message] = Field(default=None)


class UIServerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"


class RunnerStackFrame(BaseModel):
    workflow_name: str
    workflow_execution_id: str
    node_name: str
    status: state.RunnerStatus


class UIServerStatePacket(BaseModel):
    kind: typing.Literal[BasePacketKind.UI_STATE] = Field(
        default=BasePacketKind.UI_STATE
    )
    status: UIServerStatus
    runners: list[RunnerStackFrame] = Field(default_factory=list)


BasePacket = Annotated[
    typing.Union[AckPacket, RunnerReqPacket, RunnerRespPacket, UIServerStatePacket],
    Field(discriminator="kind"),
]


class BasePacketEnvelope(BaseModel):
    msg_id: int
    payload: BasePacket
    source_msg_id: Optional[int] = Field(default=None)
