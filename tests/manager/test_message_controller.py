from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest

from tests.manager.runner_stubs import DummyRunnerWithWorkflow
from tests.stub_project import StubProject
from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.history.models import HistoryMutationResult
from vocode.input_manager import INPUT_TYPE_INTERACTIVE
from vocode import settings as vocode_settings
from vocode.manager import autocomplete_providers as autocomplete_providers
from vocode.manager import proto as manager_proto
from vocode.manager.base import BaseManager, RunnerFrame
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer
from vocode.runner import proto as runner_proto


@pytest.mark.asyncio
async def test_uiserver_handles_autocomplete_request() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    req = manager_proto.AutocompleteReqPacket(text="he", row=0, col=2)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=req)
    await client_endpoint.send(envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp_envelope = await client_endpoint.recv()
    resp_payload = resp_envelope.payload
    assert resp_payload.kind == manager_proto.BasePacketKind.AUTOCOMPLETE_RESP
    assert isinstance(resp_payload, manager_proto.AutocompleteRespPacket)
    assert resp_payload.items == []


@pytest.mark.asyncio
async def test_uiserver_handles_file_autocomplete_request_without_know(
    tmp_path,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")

    project = StubProject(base_path=tmp_path)
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    req = manager_proto.AutocompleteReqPacket(text="@ma", row=0, col=3)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=req)
    await client_endpoint.send(envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp_envelope = await client_endpoint.recv()
    resp_payload = resp_envelope.payload
    assert resp_payload.kind == manager_proto.BasePacketKind.AUTOCOMPLETE_RESP
    assert isinstance(resp_payload, manager_proto.AutocompleteRespPacket)
    assert [item.title for item in resp_payload.items] == ["src/main.py"]


@pytest.mark.asyncio
async def test_uiserver_unknown_command_sends_error() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    user_message = state.Message(role=models.Role.USER, text="/does-not-exist arg")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True

    resp_envelope = await client_endpoint.recv()
    assert isinstance(resp_envelope.payload, manager_proto.TextMessagePacket)
    assert resp_envelope.payload.text == "[yellow]Unknown command: /does-not-exist[/]"


@pytest.mark.asyncio
async def test_run_autocomplete_provider_uses_workflow_name_values() -> None:
    project = StubProject()
    workflow_name = "wf-auto"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.run_autocomplete_provider(
        server,
        "/run ",
        0,
        5,
    )

    assert items is not None
    assert [item.title for item in items] == ["/run wf-auto - workflow"]
    assert [item.replace_start for item in items] == [0]
    assert [item.replace_text for item in items] == ["/run "]
    assert [item.insert_text for item in items] == [f"/run {workflow_name}"]


@pytest.mark.asyncio
async def test_run_autocomplete_provider_does_not_suggest_exact_match() -> None:
    project = StubProject()
    workflow_name = "wf-auto"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.run_autocomplete_provider(
        server,
        "/run wf-auto",
        0,
        len("/run wf-auto"),
    )
    assert items is not None
    assert [item.title for item in items] == ["/run wf-auto - workflow"]
    assert [item.replace_start for item in items] == [0]
    assert [item.replace_text for item in items] == ["/run wf-auto"]
    assert [item.insert_text for item in items] == ["/run wf-auto"]


@pytest.mark.asyncio
async def test_uiserver_handles_stop_request_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    called: list[object] = []

    async def fake_stop_current_runner() -> None:
        called.append(object())

    monkeypatch.setattr(server.manager, "stop_current_runner", fake_stop_current_runner)

    stop_packet = manager_proto.StopReqPacket()
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=stop_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert called


@pytest.mark.asyncio
async def test_uiserver_log_req_packet_paginates_and_maps_levels() -> None:
    project = StubProject()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    manager = server._log_manager
    manager.add_record(
        logging.LogRecord(
            name="logger.debug",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="debug message",
            args=(),
            exc_info=None,
        )
    )
    manager.add_record(
        logging.LogRecord(
            name="logger.info",
            level=logging.INFO,
            pathname=__file__,
            lineno=2,
            msg="info message",
            args=(),
            exc_info=None,
        )
    )
    manager.add_record(
        logging.LogRecord(
            name="logger.warning",
            level=logging.WARNING,
            pathname=__file__,
            lineno=3,
            msg="warning message",
            args=(),
            exc_info=None,
        )
    )
    manager.add_record(
        logging.LogRecord(
            name="logger.error",
            level=logging.ERROR,
            pathname=__file__,
            lineno=4,
            msg="error message",
            args=(),
            exc_info=None,
        )
    )
    manager.add_record(
        logging.LogRecord(
            name="logger.critical",
            level=logging.CRITICAL,
            pathname=__file__,
            lineno=5,
            msg="critical message",
            args=(),
            exc_info=None,
        )
    )

    packet = manager_proto.LogReqPacket(offset=1, limit=3)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)

    resp = await server._on_log_req_packet(envelope)

    assert isinstance(resp, manager_proto.LogRespPacket)
    assert resp.offset == 1
    assert resp.total >= 5
    assert len(resp.entries) == 3
    assert [entry.message for entry in resp.entries] == [
        "info message",
        "warning message",
        "error message",
    ]
    assert [entry.level for entry in resp.entries] == [
        manager_proto.LogLevel.INFO,
        manager_proto.LogLevel.WARNING,
        manager_proto.LogLevel.ERROR,
    ]


@pytest.mark.asyncio
async def test_uiserver_log_req_packet_normalizes_negative_offset_and_limit() -> None:
    project = StubProject()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    manager = server._log_manager
    manager.add_record(
        logging.LogRecord(
            name="logger.info",
            level=logging.INFO,
            pathname=__file__,
            lineno=6,
            msg="entry",
            args=(),
            exc_info=None,
        )
    )

    packet = manager_proto.LogReqPacket(offset=-10, limit=-2)
    envelope = manager_proto.BasePacketEnvelope(msg_id=2, payload=packet)

    resp = await server._on_log_req_packet(envelope)

    assert isinstance(resp, manager_proto.LogRespPacket)
    assert resp.offset == 0
    assert resp.entries == []


@pytest.mark.asyncio
async def test_uiserver_user_input_triggers_history_edit_when_no_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED
            self.execution = state.WorkflowExecution(workflow_name="wf-edit")

    runner = DummyRunner()
    node_execution = history.upsert_node_execution(
        runner.execution,
        state.NodeExecution(
            node="node",
            status=state.RunStatus.RUNNING,
        ),
    )
    message = state.Message(role=models.Role.USER, text="old")
    history.upsert_message(runner.execution, message)
    step = history.upsert_step(
        runner.execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=message.id,
            is_complete=True,
        ),
    )
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    called: list[str] = []

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
        *,
        resume: bool = True,
    ) -> HistoryMutationResult:
        _ = resume
        called.append(text)
        message.text = text
        return HistoryMutationResult(
            changed=True,
            upserted_steps=[step],
        )

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )

    user_message = state.Message(role=models.Role.USER, text="new input text")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert called == ["new input text"]

    upsert_envelope = await client_endpoint.recv()
    upsert_payload = upsert_envelope.payload
    assert upsert_payload.kind == manager_proto.BasePacketKind.RUNNER_REQ
    assert isinstance(upsert_payload, manager_proto.RunnerReqPacket)
    assert upsert_payload.step.id == step.id
    assert upsert_payload.step.message is not None
    assert upsert_payload.step.message.text == "new input text"


@pytest.mark.asyncio
async def test_uiserver_user_input_sends_error_when_edit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED

    runner = DummyRunner()
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit-fail",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
        *,
        resume: bool = True,
    ) -> HistoryMutationResult:
        _ = text
        _ = resume
        return HistoryMutationResult(changed=False)

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )

    messages: list[str] = []

    async def fake_send_text_message(
        text: str,
        text_format: manager_proto.TextMessageFormat = manager_proto.TextMessageFormat.PLAIN,
    ) -> None:
        _ = text_format
        messages.append(text)

    monkeypatch.setattr(server, "send_text_message", fake_send_text_message)

    user_message = state.Message(role=models.Role.USER, text="new input text")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=2, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert messages
    assert "Unable to edit history" in messages[0]


@pytest.mark.asyncio
async def test_uiserver_user_input_emits_step_deleted_packet_on_history_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED
            self.execution = state.WorkflowExecution(workflow_name="wf-edit")

    runner = DummyRunner()
    node_execution = history.upsert_node_execution(
        runner.execution,
        state.NodeExecution(
            node="node",
            status=state.RunStatus.RUNNING,
        ),
    )
    message = state.Message(role=models.Role.USER, text="old")
    history.upsert_message(runner.execution, message)
    step = history.upsert_step(
        runner.execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=message.id,
            is_complete=True,
        ),
    )
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit-delete",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
        *,
        resume: bool = True,
    ) -> HistoryMutationResult:
        assert text == "new input text"
        assert resume is False
        message.text = text
        removed_step_ids = [uuid4(), uuid4()]
        return HistoryMutationResult(
            changed=True,
            removed_step_ids=removed_step_ids,
            upserted_steps=[step],
        )

    called_continue: list[object] = []

    async def fake_continue_current_runner(self: BaseManager) -> object:
        called_continue.append(object())
        return object()

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )
    monkeypatch.setattr(
        BaseManager, "continue_current_runner", fake_continue_current_runner
    )

    user_message = state.Message(role=models.Role.USER, text="new input text")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True

    deleted_envelope = await client_endpoint.recv()
    assert deleted_envelope.payload.kind == manager_proto.BasePacketKind.STEP_DELETED
    deleted_payload = deleted_envelope.payload
    assert isinstance(deleted_payload, manager_proto.StepDeletedPacket)
    assert len(deleted_payload.step_ids) == 2

    upsert_envelope = await client_endpoint.recv()
    upsert_payload = upsert_envelope.payload
    assert upsert_payload.kind == manager_proto.BasePacketKind.RUNNER_REQ
    assert isinstance(upsert_payload, manager_proto.RunnerReqPacket)
    assert upsert_payload.step.id == step.id
    assert upsert_payload.step.message is not None
    assert upsert_payload.step.message.text == "new input text"
    assert called_continue


@pytest.mark.asyncio
async def test_uiserver_emits_branch_packets_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    server.enable_branch_packets()

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED
            self.execution = state.WorkflowExecution(workflow_name="wf-edit")

    runner = DummyRunner()
    node_execution = history.upsert_node_execution(
        runner.execution,
        state.NodeExecution(
            node="node",
            status=state.RunStatus.RUNNING,
        ),
    )
    message = state.Message(role=models.Role.USER, text="old")
    history.upsert_message(runner.execution, message)
    step = history.upsert_step(
        runner.execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=message.id,
            is_complete=True,
        ),
    )
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit-branch-packets",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    branch_id = uuid4()
    created_branch_id = uuid4()

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
        *,
        resume: bool = True,
    ) -> HistoryMutationResult:
        assert text == "new input text"
        assert resume is False
        message.text = text
        return HistoryMutationResult(
            changed=True,
            active_branch_id=branch_id,
            created_branch_id=created_branch_id,
            removed_step_ids=[uuid4()],
            upserted_step_ids=[step.id],
            upserted_steps=[step],
            branch_summaries=[],
        )

    async def fake_continue_current_runner(self: BaseManager) -> object:
        return object()

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )
    monkeypatch.setattr(
        BaseManager, "continue_current_runner", fake_continue_current_runner
    )

    user_message = state.Message(role=models.Role.USER, text="new input text")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    deleted_envelope = await client_endpoint.recv()
    assert isinstance(deleted_envelope.payload, manager_proto.StepDeletedPacket)

    upsert_envelope = await client_endpoint.recv()
    assert isinstance(upsert_envelope.payload, manager_proto.RunnerReqPacket)

    branch_changed_envelope = await client_endpoint.recv()
    assert isinstance(
        branch_changed_envelope.payload, manager_proto.BranchChangedPacket
    )
    assert branch_changed_envelope.payload.active_branch_id == str(branch_id)
    assert branch_changed_envelope.payload.created_branch_id == str(created_branch_id)

    diff_envelope = await client_endpoint.recv()
    assert isinstance(diff_envelope.payload, manager_proto.HistoryViewDiffPacket)
    assert diff_envelope.payload.upserted_step_ids == [str(step.id)]
    assert diff_envelope.payload.removed_step_ids


@pytest.mark.asyncio
async def test_uiserver_aa_command_autoapproves_and_confirms_tool_call() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    execution = state.WorkflowExecution(workflow_name="wf-ui-aa")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node1",
            status=state.RunStatus.RUNNING,
        ),
    )

    tool_req = state.ToolCallReq(
        id="call-1",
        name="test-tool",
        arguments={"x": 1},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    message = state.Message(
        role=models.Role.ASSISTANT,
        text="",
        tool_call_requests=[tool_req],
    )
    history.upsert_message(execution, message)
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.TOOL_REQUEST,
            message_id=message.id,
            is_complete=True,
        ),
    )
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )

    dummy_task = asyncio.create_task(asyncio.Event().wait())
    runner = DummyRunnerWithWorkflow(["node1"], execution=execution)
    frame = RunnerFrame(
        workflow_name="wf-ui-aa",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    await server.start()
    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    _ = await client_endpoint.recv()
    prompt_envelope = await client_endpoint.recv()
    prompt_payload = prompt_envelope.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    assert prompt_payload.subtitle is not None
    assert "/aa" in prompt_payload.subtitle
    waiter_task = asyncio.create_task(
        project.input_manager.wait_for_input(input_type=INPUT_TYPE_INTERACTIVE)
    )
    await asyncio.sleep(0)

    user_message = state.Message(role=models.Role.USER, text="/aa")
    user_input_packet = manager_proto.UserInputPacket(message=user_message)
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=prompt_envelope.msg_id + 1,
        payload=user_input_packet,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    published_message = await waiter_task
    assert published_message.text == ""

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP
    assert project.project_state.autoapprove.should_auto_approve("test-tool", {"x": 1})

    clear_prompt_envelope = await client_endpoint.recv()
    assert isinstance(clear_prompt_envelope.payload, manager_proto.InputPromptPacket)

    text_envelope = await client_endpoint.recv()
    assert isinstance(text_envelope.payload, manager_proto.TextMessagePacket)

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_user_input_sends_error_when_no_active_input_request() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    user_message = state.Message(role=models.Role.USER, text="orphan input")
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True

    resp_envelope = await client_endpoint.recv()
    assert isinstance(resp_envelope.payload, manager_proto.TextMessagePacket)
    assert "Input was rejected" in resp_envelope.payload.text
