from __future__ import annotations

from enum import Enum
from typing import Annotated, Optional, Union

from pydantic import BaseModel, Field


class BasePacketKind(str, Enum):
    ACK = "ack"


class AckPacket(BaseModel):
    kind: BasePacketKind = Field(default=BasePacketKind.ACK)


BasePacket = Annotated[Union[AckPacket], Field(discriminator="kind")]


class BasePacketEnvelope(BaseModel):
    msg_id: int
    payload: BasePacket
    source_msg_id: Optional[int] = Field(default=None)
