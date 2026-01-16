import asyncio
import datetime
import os
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


def test_codec_roundtrip_allows_auto_approved_bool():
    run = state.WorkflowExecution(workflow_name="wf")
    msg = state.Message(role=models.Role.USER, text="hi")
    msg.tool_call_requests.append(
        state.ToolCallReq(
            id="call_1",
            name="foo",
            arguments={},
            auto_approved=True,
        )
    )
    ne1 = state.NodeExecution(
        node="n1",
        input_messages=[msg],
        status=state.RunStatus.RUNNING,
    )
    run.node_executions[ne1.id] = ne1
    run.touch()

    blob = persistence_codec.dumps_gzip(run)
    restored = persistence_codec.loads_gzip(blob)
    restored_msg = restored.node_executions[ne1.id].input_messages[0]
    assert restored_msg.tool_call_requests[0].auto_approved is True


@pytest.mark.asyncio
async def test_state_manager_flushes_to_expected_session_layout(tmp_path):
    session_id = uuid.uuid4().hex
    mgr = persistence_state_manager.WorkflowStateManager(
        base_path=tmp_path,
        session_id=session_id,
        save_interval_s=0.05,
    )
    await mgr.start()
    date_prefix = datetime.datetime.now().strftime("%Y_%m_%d")
    assert mgr.session_dir.name == f"{date_prefix}_1_{session_id}"
    run = _build_sample_execution()
    mgr.track(run)
    mgr.notify_changed(run)
    await asyncio.sleep(0.15)
    await mgr.shutdown()

    expected = mgr.session_dir / f"{run.id}.json.gz"
    assert expected.exists()
    loaded = persistence_codec.load_from_path(expected)
    assert loaded.id == run.id
 

@pytest.mark.asyncio
async def test_state_manager_session_dir_sequence_number_increments(tmp_path):
    sessions_root = tmp_path / ".vocode" / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    date_prefix = datetime.datetime.now().strftime("%Y_%m_%d")
    (sessions_root / f"{date_prefix}_1_aaa").mkdir(parents=True, exist_ok=True)
    (sessions_root / f"{date_prefix}_3_bbb").mkdir(parents=True, exist_ok=True)

    session_id = uuid.uuid4().hex
    mgr = persistence_state_manager.WorkflowStateManager(
        base_path=tmp_path,
        session_id=session_id,
        save_interval_s=9999.0,
    )
    await mgr.start()
    assert mgr.session_dir.name == f"{date_prefix}_4_{session_id}"
    await mgr.shutdown()
 
@pytest.mark.asyncio
async def test_state_manager_prunes_old_sessions_when_over_max_total_log_bytes(tmp_path):
    sessions_root = tmp_path / ".vocode" / "sessions"
    old_dir = sessions_root / "2000_01_01_old"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "old.bin"
    old_file.write_bytes(b"x" * 2000)
    os.utime(old_file, (1, 1))
    os.utime(old_dir, (1, 1))

    session_id = uuid.uuid4().hex
    mgr = persistence_state_manager.WorkflowStateManager(
        base_path=tmp_path,
        session_id=session_id,
        save_interval_s=9999.0,
        max_total_log_bytes=2500,
    )
    await mgr.start()

    (mgr.session_dir / "dummy.bin").write_bytes(b"y" * 2000)
    run = _build_sample_execution()
    mgr.track(run)
    mgr.notify_changed(run)
    await mgr.shutdown()

    assert not old_dir.exists()
    assert mgr.session_dir.exists()
    expected = mgr.session_dir / f"{run.id}.json.gz"
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

    expected = mgr.session_dir / f"{run.id}.json.gz"
    assert expected.exists()