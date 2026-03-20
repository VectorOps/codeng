import asyncio
import datetime
import os
import uuid

import pytest

from vocode import models
from vocode import state
from vocode.history.manager import HistoryManager
from vocode.persistence import codec as persistence_codec
from vocode.persistence import state_manager as persistence_state_manager


def _build_sample_execution() -> state.WorkflowExecution:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    input_message = state.Message(role=models.Role.USER, text="hi")
    ne1 = history.create_node_execution(
        run,
        node="n1",
        input_messages=[input_message],
        status=state.RunStatus.RUNNING,
    )
    s1 = history.create_step(
        run,
        execution_id=ne1.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="hello"),
        is_complete=True,
        is_final=True,
    )
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
    restored_steps = tuple(restored.iter_steps())
    assert len(restored_steps) == 1
    step = restored_steps[0]
    assert step.execution_id in restored.node_executions
    assert step.execution is restored.node_executions[step.execution_id]
    assert restored.updated_at == run.updated_at


def test_codec_roundtrip_allows_auto_approved_bool():
    history = HistoryManager()
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
    ne1 = history.create_node_execution(
        run,
        node="n1",
        input_messages=[msg],
        status=state.RunStatus.RUNNING,
    )
    run.touch()

    blob = persistence_codec.dumps_gzip(run)
    restored = persistence_codec.loads_gzip(blob)
    restored_msg = restored.node_executions[ne1.id].input_messages[0]
    assert restored_msg.tool_call_requests[0].auto_approved is True


def test_codec_roundtrip_loaded_state_supports_explicit_id_updates():
    history = HistoryManager()
    run = _build_sample_execution()
    restored = persistence_codec.loads_gzip(persistence_codec.dumps_gzip(run))

    node_execution = next(iter(restored.node_executions.values()))
    new_message = state.Message(role=models.Role.USER, text="follow-up")
    history.add_message(restored, new_message)
    node_execution.input_message_ids.append(new_message.id)

    new_step_message = state.Message(
        role=models.Role.ASSISTANT, text="follow-up-response"
    )
    history.add_message(restored, new_step_message)
    new_step = history.create_step(
        restored,
        execution_id=node_execution.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=new_step_message.id,
    )

    assert node_execution.input_message_ids[-1] == new_message.id
    assert restored.step_ids[-1] == new_step.id
    assert node_execution.step_ids[-1] == new_step.id


def test_codec_roundtrip_restores_visible_step_ids_from_branch_projection():
    history = HistoryManager()
    execution = state.WorkflowExecution(workflow_name="wf")
    node_execution = history.create_node_execution(
        execution,
        node="node",
        status=state.RunStatus.RUNNING,
    )
    message1 = state.Message(role=models.Role.USER, text="one")
    history.add_message(execution, message1)
    step1 = history.create_step(
        execution,
        execution_id=node_execution.id,
        type=state.StepType.INPUT_MESSAGE,
        message_id=message1.id,
        is_complete=True,
    )
    message2 = state.Message(role=models.Role.USER, text="two")
    history.add_message(execution, message2)
    step2 = history.create_step(
        execution,
        execution_id=node_execution.id,
        type=state.StepType.INPUT_MESSAGE,
        message_id=message2.id,
        is_complete=True,
    )
    branch1_id = execution.get_active_branch().id
    branch2 = history.create_branch(
        execution,
        head_step_id=step1.id,
        base_step_id=step2.id,
        activate=True,
    )
    branched_execution = history.create_node_execution(
        execution,
        node=node_execution.node,
        status=state.RunStatus.RUNNING,
        branch_id=branch2.id,
        input_message_ids=list(node_execution.input_message_ids),
        previous_id=node_execution.previous_id,
    )
    message3 = state.Message(role=models.Role.USER, text="three")
    history.add_message(execution, message3)
    step3 = history.create_step(
        execution,
        execution_id=branched_execution.id,
        parent_step_id=step1.id,
        type=state.StepType.INPUT_MESSAGE,
        message_id=message3.id,
        is_complete=True,
    )

    restored = persistence_codec.loads_gzip(persistence_codec.dumps_gzip(execution))

    assert restored.step_ids == [step1.id, step3.id]
    assert restored.get_node_execution(node_execution.id).step_ids == [
        step1.id,
        step2.id,
    ]
    assert restored.get_node_execution(branched_execution.id).step_ids == [step3.id]
    history.switch_branch(restored, branch1_id)
    assert restored.step_ids == [step1.id, step2.id]


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
async def test_state_manager_prunes_old_sessions_when_over_max_total_log_bytes(
    tmp_path,
):
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
