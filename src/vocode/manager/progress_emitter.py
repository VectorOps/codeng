from __future__ import annotations

import time
import uuid
from typing import Optional

from . import proto as manager_proto
from .interfaces import UIPacketSender


class ProgressEmitter:
    def __init__(self, packet_sender: UIPacketSender) -> None:
        self._packet_sender = packet_sender
        self._last_sent_at_by_id: dict[str, float] = {}

    async def emit_start(
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
        await self._packet_sender.send_packet(
            manager_proto.ProgressPacket(
                progress_id=resolved_id,
                status=manager_proto.ProgressStatus.START,
                title=title,
                message=message,
                mode=mode,
                bar_type=bar_type,
                on_complete=on_complete,
                complete_message=complete_message,
            )
        )
        self._last_sent_at_by_id.pop(resolved_id, None)

    async def emit_end(
        self,
        *,
        progress_id: str,
        on_complete: Optional[manager_proto.ProgressOnComplete] = None,
        complete_message: Optional[str] = None,
    ) -> None:
        await self._packet_sender.send_packet(
            manager_proto.ProgressPacket(
                progress_id=progress_id,
                status=manager_proto.ProgressStatus.END,
                done=True,
                on_complete=on_complete,
                complete_message=complete_message,
            )
        )

    async def emit_update(
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
        last = self._last_sent_at_by_id.get(progress_id)
        if last is not None and (now - last) < min_interval_s:
            return
        self._last_sent_at_by_id[progress_id] = now

        await self._packet_sender.send_packet(
            manager_proto.ProgressPacket(
                progress_id=progress_id,
                status=manager_proto.ProgressStatus.UPDATE,
                title=title,
                message=message,
                mode=(
                    mode
                    if mode is not None
                    else manager_proto.ProgressMode.DETERMINISTIC
                ),
                bar_type=(
                    bar_type
                    if bar_type is not None
                    else manager_proto.ProgressBarType.BAR
                ),
                completed=completed,
                total=total,
                unit=unit,
                done=done,
                on_complete=on_complete,
                complete_message=complete_message,
            )
        )
