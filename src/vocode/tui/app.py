from __future__ import annotations

import asyncio
from pathlib import Path
import typing

import click

from vocode.manager import helpers as manager_helpers
from vocode.manager import proto as manager_proto
from vocode.manager import server as manager_server
from vocode import project as vocode_project
from vocode.tui import uistate as tui_uistate


class App:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._endpoint_ui, self._endpoint_server = (
            manager_helpers.InMemoryEndpoint.pair()
        )

        project = vocode_project.Project.from_base_path(self._project_path)
        self._ui_server = manager_server.UIServer(
            project=project,
            endpoint=self._endpoint_server,
        )

        self._rpc = manager_helpers.RpcHelper(
            send_callback=self._endpoint_ui.send,
            name="tui-rpc",
        )
        self._router = manager_helpers.IncomingPacketRouter(
            rpc=self._rpc,
            name="tui-router",
        )

        self._register_handlers()

        self._state = tui_uistate.TUIState(on_input=self.on_input)

    def _register_handlers(self) -> None:
        for kind in manager_proto.BasePacketKind:
            self._router.register(kind, self._handle_packet_noop)

    async def _handle_packet_noop(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        _ = envelope
        return None

    async def on_input(self, text: str) -> None:
        _ = text

    async def _recv_loop(self) -> None:
        while True:
            envelope = await self._endpoint_ui.recv()
            handled = await self._router.handle(envelope)
            if not handled:
                continue

    async def run(self) -> None:
        recv_task = asyncio.create_task(self._recv_loop())
        await self._state.start()
        try:
            await recv_task
        finally:
            await self._state.stop()
            recv_task.cancel()


@click.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
def main(project_path: Path) -> None:
    app = App(project_path)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
