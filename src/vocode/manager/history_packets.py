from __future__ import annotations

from vocode.history.models import HistoryMutationResult

from . import proto as manager_proto
from .base import RunnerFrame
from .interfaces import UIPacketSender


class HistoryMutationPacketEmitter:
    def __init__(
        self,
        packet_sender: UIPacketSender,
        *,
        emit_branch_packets: bool = False,
    ) -> None:
        self._packet_sender = packet_sender
        self._emit_branch_packets = emit_branch_packets

    @property
    def emit_branch_packets(self) -> bool:
        return self._emit_branch_packets

    @emit_branch_packets.setter
    def emit_branch_packets(self, value: bool) -> None:
        self._emit_branch_packets = value

    def _build_branch_summaries(
        self,
        result: HistoryMutationResult,
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

    async def emit(
        self,
        frame: RunnerFrame,
        result: HistoryMutationResult,
    ) -> None:
        execution = frame.runner.execution
        if result.removed_step_ids:
            await self._packet_sender.send_packet(
                manager_proto.StepDeletedPacket(
                    step_ids=[str(step_id) for step_id in result.removed_step_ids]
                )
            )
        for upsert_step in result.upserted_steps:
            await self._packet_sender.send_packet(
                manager_proto.RunnerReqPacket(
                    workflow_id=frame.workflow_name,
                    workflow_name=execution.workflow_name,
                    workflow_execution_id=str(execution.id),
                    step=upsert_step,
                    input_required=False,
                    display=None,
                )
            )
        if not self._emit_branch_packets:
            return
        if result.active_branch_id is not None:
            await self._packet_sender.send_packet(
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
            await self._packet_sender.send_packet(
                manager_proto.BranchListPacket(
                    workflow_execution_id=str(execution.id),
                    branches=self._build_branch_summaries(result),
                )
            )
        await self._packet_sender.send_packet(
            manager_proto.HistoryViewDiffPacket(
                workflow_execution_id=str(execution.id),
                removed_step_ids=[str(step_id) for step_id in result.removed_step_ids],
                upserted_step_ids=[
                    str(step_id) for step_id in result.upserted_step_ids
                ],
            )
        )
