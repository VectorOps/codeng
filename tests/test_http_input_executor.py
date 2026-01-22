from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from aiohttp import ClientSession

from vocode import models, state, settings as vocode_settings
from vocode.http import server as http_server
from vocode.runner.executors.http_input import HTTPInputNode
from vocode.runner.runner import Runner, RunEvent
from vocode.runner.proto import RunEventResp, RunEventResponseType
from tests.stub_project import StubProject


async def _drive_runner(
    agen: AsyncIterator[RunEvent],
) -> list[RunEvent]:
    events: list[RunEvent] = []
    send: RunEventResp | None = None
    while True:
        try:
            if send is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(send)
        except StopAsyncIteration:
            break
        events.append(event)
        if event.step is None:
            send = RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
            continue
        step = event.step
        if step.type in (
            state.StepType.PROMPT,
            state.StepType.PROMPT_CONFIRM,
        ):
            send = RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        else:
            send = RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
    return events


@pytest.mark.asyncio
async def test_http_input_executor_waits_for_external_message(tmp_path) -> None:
    settings = vocode_settings.Settings(
        internal_http=vocode_settings.InternalHTTPSettings(host="127.0.0.1", port=0)
    )
    http_server.configure_internal_http(settings.internal_http)  # type: ignore[arg-type]

    project = StubProject(settings=settings)

    node = HTTPInputNode(
        name="http-input-node",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
        path="/http-input-test",
        message="Waiting for external input",
    )
    graph = models.Graph(nodes=[node], edges=[])

    class Workflow:
        def __init__(self) -> None:
            self.name = "wf-http-input"
            self.graph = graph
            self.need_input = False
            self.need_input_prompt = None

    workflow = Workflow()

    runner = Runner(
        workflow=workflow,
        project=project,
        initial_message=None,
    )

    agen = runner.run()

    async def send_request() -> None:
        await asyncio.sleep(0.05)
        while not http_server.is_running():
            await asyncio.sleep(0.01)
        srv = http_server.get_internal_http_server()
        runner_http = srv._runner
        assert runner_http is not None
        sites = list(runner_http.sites)
        assert sites
        site = sites[0]
        sockets = list(site._server.sockets) if site._server is not None else []
        assert sockets
        host, port = sockets[0].getsockname()[:2]
        async with ClientSession() as session:
            async with session.post(
                f"http://{host}:{port}{node.path}",
                json={"text": "from-http"},
            ) as resp:
                assert resp.status == 200

    sender_task = asyncio.create_task(send_request())

    events = await _drive_runner(agen)

    await sender_task

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"http-input-node"}
    exec_item = node_execs_by_name["http-input-node"]

    output_steps = [
        s
        for s in exec_item.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.message is not None
    ]
    assert output_steps
    assert any(
        s.message is not None and s.message.text == "from-http" for s in output_steps
    )

    assert any(
        e.step is not None
        and e.step.type == state.StepType.OUTPUT_MESSAGE
        and e.step.message is not None
        and e.step.message.text == "Waiting for external input"
        for e in events
    )
