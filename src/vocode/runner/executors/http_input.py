from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from aiohttp import web
from pydantic import Field

from vocode import models, state
from vocode.http import server as http_server
from vocode.runner import base as runner_base


class HTTPInputNode(models.Node):
    type: str = "http-input"

    path: str = Field(
        default="/input",
        description="HTTP path used to receive external input messages.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional status message emitted while waiting for HTTP input.",
    )


@runner_base.ExecutorFactory.register("http-input")
class HTTPInputExecutor(runner_base.BaseExecutor):
    def __init__(self, config: HTTPInputNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config
        self._route_handle: Optional[http_server.RouteHandle] = None
        self._queue_key = f"http-input:{self.config.name}"

    async def init(self) -> None:
        queue = self.project.project_state.get(self._queue_key)
        if queue is None:
            queue = asyncio.Queue()
            self.project.project_state.set(self._queue_key, queue)

        async def handler(request: web.Request) -> web.StreamResponse:
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "invalid_json"}, status=400)

            text = data.get("text")
            if not isinstance(text, str):
                return web.json_response(
                    {"error": "missing_text"}, status=400
                )

            role_value = data.get("role", models.Role.USER.value)
            try:
                role = models.Role(role_value)
            except ValueError:
                role = models.Role.USER

            message = state.Message(
                role=role,
                text=text,
            )
            await queue.put(message)
            return web.json_response({"status": "ok"})

        protected = http_server.require_internal_auth(handler)
        self._route_handle = await http_server.add_route(
            "POST",
            self.config.path,
            protected,
        )

    async def shutdown(self) -> None:
        if self._route_handle is not None:
            await http_server.remove_route(self._route_handle)
            self._route_handle = None

    async def run(self, inp: runner_base.ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution

        queue = self.project.project_state.get(self._queue_key)
        if queue is None:
            queue = asyncio.Queue()
            self.project.project_state.set(self._queue_key, queue)

        waiting_text = self.config.message or "Waiting for HTTP input..."
        waiting_message = state.Message(
            role=models.Role.ASSISTANT,
            text=waiting_text,
        )
        waiting_step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=waiting_message,
            is_complete=False,
        )
        yield waiting_step

        message = await queue.get()
        output_step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=message,
            is_complete=True,
            is_final=True,
        )
        yield output_step
