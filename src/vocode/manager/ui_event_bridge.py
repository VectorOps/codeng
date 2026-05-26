from __future__ import annotations

from vocode import ui_events
from vocode.project import Project

from . import proto as manager_proto
from .interfaces import UIPacketSender


class ProjectUIEventBridge:
    def __init__(
        self,
        *,
        project: Project,
        packet_sender: UIPacketSender,
    ) -> None:
        self._project = project
        self._packet_sender = packet_sender

    async def on_project_ui_event(self, event: ui_events.ProjectUIEvent) -> None:
        await self._packet_sender.send_packet(manager_proto.UIEventPacket(event=event))

    def start(self) -> None:
        self._project.subscribe_ui_events(self.on_project_ui_event)

    def stop(self) -> None:
        self._project.unsubscribe_ui_events(self.on_project_ui_event)
