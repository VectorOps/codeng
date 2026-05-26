from __future__ import annotations

from typing import Optional, Protocol

from vocode import state
from vocode.history import models as history_models
from vocode.project import Project
from vocode.runner.runner import Runner

from . import proto as manager_proto
from .base import RunnerFrame


class UIPacketSender(Protocol):
    async def send_packet(self, payload: manager_proto.BasePacket) -> None: ...

    async def send_text_message(
        self,
        text: str,
        text_format: manager_proto.TextMessageFormat = manager_proto.TextMessageFormat.PLAIN,
    ) -> None: ...


class UIManager(Protocol):
    project: Project

    @property
    def runner_stack(self) -> list[RunnerFrame]: ...

    @property
    def current_runner(self) -> Optional[Runner]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def start_workflow(
        self,
        workflow_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> Runner: ...

    async def stop_all_runners(self) -> None: ...

    async def stop_current_runner(self) -> None: ...

    async def continue_current_runner(self) -> Runner: ...

    async def restart_current_runner(
        self,
        initial_message: Optional[state.Message] = None,
    ) -> Runner: ...

    async def edit_history_with_text(
        self,
        text: str,
        *,
        resume: bool = True,
    ) -> history_models.HistoryMutationResult: ...
