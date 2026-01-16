import asyncio
import uuid

import pytest

from vocode import models
from vocode import state
from vocode.persistence import codec as persistence_codec
from vocode.persistence import state_manager as persistence_state_manager


def _build_sample_execution() -> state.WorkflowExecution:
    run = state.WorkflowExecution(workflow_name="wf")
    ne1 = state.NodeExecution(
        node="n1",
        input_messages=[
            state.Message(role=models.Role.USER, text="hi"),
        ],
        status=state.RunStatus.RUNNING,
    )
    run.node_executions[ne1.id] = ne1
    s1 = state.Step(
        execution=ne1,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="hello"),
        is_complete=True,
        is_final=True,
    )
    ne1.steps.append(s1)
    run.steps.append(s1)
    run.touch()
    return run


def test_workflow_execution_touch_updates_updated_at():
    run = state.WorkflowExecution(workflow_name="wf")
    before = run.updated_at
    run.touch()
    assert run.updated_at >= before


def test_codec_roundtrip_is_acyclic_and_restores_links():
    run = _build_sample_execution()
    blob = persistence_codec.dumps_gzip(run)
    restored = persistence_codec.loads_gzip(blob)

    assert restored.id == run.id
    assert restored.workflow_name == run.workflow_name
    assert len(restored.steps) == 1
    step = restored.steps[0]
    assert step.execution.id in restored.node_executions
    assert step.execution is restored.node_executions[step.execution.id]
    assert restored.updated_at == run.updated_at


@pytest.mark.asyncio
async def test_state_manager_flushes_to_expected_session_layout(tmp_path):
    session_id = uuid.uuid4().hex
    mgr = persistence_state_manager.WorkflowStateManager(
        base_path=tmp_path,
        session_id=session_id,
        save_interval_s=0.05,
    )
    await mgr.start()
    run = _build_sample_execution()
    mgr.track(run)
    mgr.notify_changed(run)
    await asyncio.sleep(0.15)
    await mgr.shutdown()

    expected = tmp_path / ".vocode" / "sessions" / session_id / f"{run.id}.json.gz"
    assert expected.exists()
    loaded = persistence_codec.load_from_path(expected)
    assert loaded.id == run.id


@pytest.mark.asyncio
async def test_state_manager_shutdown_flushes_without_waiting_interval(tmp_path):
    session_id = uuid.uuid4().hex
    mgr = persistence_state_manager.WorkflowStateManager(
        base_path=tmp_path,
        session_id=session_id,
        save_interval_s=9999.0,
    )
    await mgr.start()
    run = _build_sample_execution()
    mgr.track(run)
    mgr.notify_changed(run)
    await mgr.shutdown()

    expected = tmp_path / ".vocode" / "sessions" / session_id / f"{run.id}.json.gz"
    assert expected.exists()