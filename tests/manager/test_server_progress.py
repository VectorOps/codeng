from __future__ import annotations

import asyncio

import pytest

from tests.stub_project import StubProject
from vocode.manager import proto as manager_proto
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer


@pytest.mark.asyncio
async def test_uiserver_progress_start_update_end_packets() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server._emit_progress_start(
        progress_id="progress:test",
        title="Working",
        message="starting",
        mode=manager_proto.ProgressMode.INDETERMINATE,
        bar_type=manager_proto.ProgressBarType.SPINNER,
    )

    await server.emit_progress_update(
        progress_id="progress:test",
        title="Working",
        message="halfway",
        mode=manager_proto.ProgressMode.DETERMINISTIC,
        bar_type=manager_proto.ProgressBarType.BAR,
        completed=5,
        total=10,
        unit="items",
        done=False,
        min_interval_s=0.0,
    )

    await server._emit_progress_end(
        progress_id="progress:test",
        on_complete=manager_proto.ProgressOnComplete.MESSAGE,
        complete_message="done",
    )

    start_envelope = await client_endpoint.recv()
    assert isinstance(start_envelope.payload, manager_proto.ProgressPacket)
    assert start_envelope.payload.status == manager_proto.ProgressStatus.START
    assert start_envelope.payload.progress_id == "progress:test"
    assert start_envelope.payload.bar_type == manager_proto.ProgressBarType.SPINNER

    update_envelope = await client_endpoint.recv()
    assert isinstance(update_envelope.payload, manager_proto.ProgressPacket)
    assert update_envelope.payload.status == manager_proto.ProgressStatus.UPDATE
    assert update_envelope.payload.completed == 5
    assert update_envelope.payload.total == 10
    assert update_envelope.payload.unit == "items"

    end_envelope = await client_endpoint.recv()
    assert isinstance(end_envelope.payload, manager_proto.ProgressPacket)
    assert end_envelope.payload.status == manager_proto.ProgressStatus.END
    assert end_envelope.payload.done is True
    assert end_envelope.payload.complete_message == "done"


@pytest.mark.asyncio
async def test_uiserver_progress_update_throttles_repeated_packets() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server.emit_progress_update(
        progress_id="progress:throttle",
        title="Working",
        completed=1,
        total=10,
        min_interval_s=60.0,
    )
    await server.emit_progress_update(
        progress_id="progress:throttle",
        title="Working",
        completed=2,
        total=10,
        min_interval_s=60.0,
    )

    envelope = await client_endpoint.recv()
    assert isinstance(envelope.payload, manager_proto.ProgressPacket)
    assert envelope.payload.status == manager_proto.ProgressStatus.UPDATE
    assert envelope.payload.completed == 1

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client_endpoint.recv(), timeout=0.01)
