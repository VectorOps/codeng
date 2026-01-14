from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import typing

import click

from vocode import models, state
from vocode.logger import logger
from vocode.manager import helpers as manager_helpers
from vocode.manager import proto as manager_proto
from vocode.manager import server as manager_server
from vocode import project as vocode_project
from vocode.tui import uistate as tui_uistate
from vocode.tui.screens import log_view as tui_log_view


@dataclass
class PromptMeta:
    title: str | None = None
    subtitle: str | None = None


class App:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._endpoint_ui, self._endpoint_server = (
            manager_helpers.InMemoryEndpoint.pair()
        )
        self._push_msg_id = 0
        self._prompt: PromptMeta | None = None
        self._recv_task: asyncio.Task[None] | None = None

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

        state_kwargs: dict[str, typing.Any] = {
            "on_input": self.on_input,
            "on_autocomplete_request": self.on_autocomplete_request,
            "on_stop": self.on_stop_request,
            "on_eof": self.on_eof_request,
        }
        state_type = getattr(tui_uistate, "TUIState")
        init_params = typing.get_type_hints(state_type.__init__)
        if "on_open_logs" in init_params:
            state_kwargs["on_open_logs"] = self.open_logs
        self._state = tui_uistate.TUIState(**state_kwargs)

    def _next_msg_id(self) -> int:
        self._push_msg_id += 1
        return self._push_msg_id

    def _set_prompt(self, prompt: PromptMeta | None) -> None:
        self._prompt = prompt
        if prompt is None:
            self._state.set_input_panel_title(None, None)
            return
        self._state.set_input_panel_title(prompt.title, prompt.subtitle)

    async def run(self) -> None:
        await self._state.start()
        await self._ui_server.start()

        self._recv_task = asyncio.create_task(self._recv_loop())
        try:
            await self._recv_task
        except asyncio.CancelledError:
            pass
        finally:
            await self._state.stop()
            if self._recv_task is not None:
                self._recv_task.cancel()
            await self._ui_server.stop()
            self._recv_task = None

    # Network packet hanbdlers
    def _register_handlers(self) -> None:
        self._router.register(
            manager_proto.BasePacketKind.RUNNER_REQ,
            self._handle_packet_runner_req,
        )
        self._router.register(
            manager_proto.BasePacketKind.UI_STATE,
            self._handle_packet_ui_state,
        )
        self._router.register(
            manager_proto.BasePacketKind.INPUT_PROMPT,
            self._handle_packet_input_prompt,
        )
        self._router.register(
            manager_proto.BasePacketKind.AUTOCOMPLETE_RESP,
            self._handle_packet_autocomplete_resp,
        )
        self._router.register(
            manager_proto.BasePacketKind.TEXT_MESSAGE,
            self._handle_packet_text_message,
        )

    async def _handle_packet_noop(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        _ = envelope
        return None

    async def _handle_packet_runner_req(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if not isinstance(payload, manager_proto.RunnerReqPacket):
            return None
        step = payload.step
        # Prompt presentation is driven by INPUT_PROMPT packets; ignore
        # any input_* fields on RunnerReq.
        self._state.handle_step(step)
        return None

    async def _handle_packet_ui_state(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if not isinstance(payload, manager_proto.UIServerStatePacket):
            return None
        self._state.handle_ui_state(payload)
        return None

    async def _handle_packet_input_prompt(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if not isinstance(payload, manager_proto.InputPromptPacket):
            return None
        # If both title and subtitle are None, clear the prompt. Otherwise,
        # set the prompt to the provided values.
        if payload.title is None and payload.subtitle is None:
            self._set_prompt(None)
            return None
        prompt = PromptMeta(
            title=payload.title,
            subtitle=payload.subtitle,
        )
        self._set_prompt(prompt)
        return None

    async def _handle_packet_autocomplete_resp(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if not isinstance(payload, manager_proto.AutocompleteRespPacket):
            return None
        self._state.handle_autocomplete_options(payload.items)
        return None

    async def _handle_packet_text_message(
        self, envelope: manager_proto.BasePacketEnvelope
    ) -> typing.Optional[manager_proto.BasePacket]:
        payload = envelope.payload
        if not isinstance(payload, manager_proto.TextMessagePacket):
            return None
        self._state.add_text_message(
            payload.text,
            text_format=payload.format.value,
        )
        return None

    async def _recv_loop(self) -> None:
        while True:
            envelope = await self._endpoint_ui.recv()
            handled = await self._router.handle(envelope)
            if not handled:
                continue

    # UI handlers
    async def on_input(self, text: str) -> None:
        message = state.Message(
            role=models.Role.USER,
            text=text,
        )
        packet = manager_proto.UserInputPacket(message=message)
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_msg_id(),
            payload=packet,
        )

        await self._endpoint_ui.send(envelope)

    async def open_logs(self) -> None:
        terminal = self._state.terminal
        if terminal.has_screens:
            screen = terminal.top_screen
            if isinstance(screen, tui_log_view.LogViewScreen):
                return
        packet = manager_proto.LogReqPacket(offset=0, limit=None)
        resp = await self._rpc.call(packet)
        if not isinstance(resp, manager_proto.LogRespPacket):
            return
        screen = tui_log_view.LogViewScreen(
            app=self,
            terminal=terminal,
            entries=resp.entries,
        )
        terminal.push_screen(screen)

    async def on_eof_request(self) -> None:
        recv_task = self._recv_task
        if recv_task is not None:
            recv_task.cancel()

    async def on_stop_request(self) -> None:
        packet = manager_proto.StopReqPacket()
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_msg_id(),
            payload=packet,
        )
        await self._endpoint_ui.send(envelope)

    async def on_autocomplete_request(self, text: str, row: int, col: int) -> None:
        packet = manager_proto.AutocompleteReqPacket(text=text, row=row, col=col)
        envelope = manager_proto.BasePacketEnvelope(
            msg_id=self._next_msg_id(),
            payload=packet,
        )
        await self._endpoint_ui.send(envelope)


@click.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
def main(project_path: Path) -> None:
    async def _run() -> None:
        app = App(project_path)
        await app.run()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
