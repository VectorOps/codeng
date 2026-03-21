from __future__ import annotations

import asyncio

import pytest

from vocode import models, state
from vocode import settings as vocode_settings
from vocode.history.manager import HistoryManager
from vocode.manager import helpers as manager_helpers
from vocode.manager import proto as manager_proto
from vocode.manager.commands import CommandManager, command, option
from vocode.manager.server import UIServer
from vocode.manager.commands import workflows as workflow_commands
from vocode.manager import base as manager_base
from vocode.runner import base as runner_base
from tests.stub_project import StubProject


class _FakeRepo:
    def __init__(self, name: str, root_path: str) -> None:
        self.id = name
        self.name = name
        self.root_path = root_path


class _FakeRepoRepo:
    def __init__(self, repos: dict[str, _FakeRepo]) -> None:
        self._repos = repos

    async def get_by_ids(self, item_ids):
        return [self._repos[i] for i in item_ids if i in self._repos]

    async def get_by_name(self, name: str):
        return self._repos.get(name)


class _FakeKnowData:
    def __init__(self, repos: dict[str, _FakeRepo]) -> None:
        self.repo = _FakeRepoRepo(repos)


class _FakeKnowPM:
    def __init__(self) -> None:
        self._repos: dict[str, _FakeRepo] = {
            "main": _FakeRepo("main", "/tmp/main"),
            "other": _FakeRepo("other", "/tmp/other"),
        }
        self.repo_ids = ["main", "other"]
        self.data = _FakeKnowData(self._repos)
        self.added: list[tuple[str, str]] = []

    async def add_repo_path(self, name: str, path: str):
        self.added.append((name, path))
        repo = _FakeRepo(name, path)
        self._repos[name] = repo
        if name not in self.repo_ids:
            self.repo_ids.append(name)
        return repo


class _FakeKnowProject:
    def __init__(self) -> None:
        self.pm = _FakeKnowPM()
        self.refreshed: list[str] = []
        self.refreshed_all = 0
        self.default_progress_callback = None

    async def refresh(self, repo=None, progress_callback=None):
        if repo is None:
            self.refreshed.append("<default>")
        else:
            self.refreshed.append(repo.name)
        if progress_callback is not None:

            class _Evt:
                repo_id = repo.id if repo is not None else ""
                total_files = 10
                processed_files = 10
                files_added = 0
                files_updated = 0
                files_deleted = 0
                elapsed_seconds = 2.5

            progress_callback(_Evt())

    async def refresh_all(self, progress_callback=None):
        self.refreshed_all += 1
        if progress_callback is not None:

            class _Evt:
                repo_id = "main"
                total_files = 10
                processed_files = 10
                files_added = 0
                files_updated = 0
                files_deleted = 0
                elapsed_seconds = 2.5

            progress_callback(_Evt())


@runner_base.ExecutorFactory.register("resume-skip")
class _ResumeSkipExecutor(runner_base.BaseExecutor):
    async def run(
        self, inp: runner_base.ExecutorInput
    ) -> runner_base.AsyncIterator[state.Step]:
        message = state.Message(role=models.Role.ASSISTANT, text="resumed")
        self.project.history.add_message(inp.run, message)
        yield self.project.history.upsert_step(
            inp.run,
            state.Step(
                execution_id=inp.execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
                message_id=message.id,
                is_complete=True,
            ),
        )


@pytest.mark.asyncio
async def test_know_progress_uses_repo_name_not_id() -> None:
    class _Repo:
        def __init__(self) -> None:
            self.id = "repo_7f2c9a"
            self.name = "main"
            self.root_path = "/tmp/main"

    class _RepoRepo:
        async def get_by_ids(self, item_ids):
            if "repo_7f2c9a" in item_ids:
                return [_Repo()]
            return []

    class _Data:
        def __init__(self) -> None:
            self.repo = _RepoRepo()

    class _PM:
        def __init__(self) -> None:
            self.repo_ids = ["repo_7f2c9a"]
            self.data = _Data()

    class _Know:
        def __init__(self) -> None:
            self.pm = _PM()
            self.default_progress_callback = None

        async def refresh_all(self, progress_callback=None):
            if progress_callback is not None:

                class _Evt:
                    repo_id = "repo_7f2c9a"
                    total_files = 10
                    processed_files = 10
                    files_added = 0
                    files_updated = 0
                    files_deleted = 0
                    elapsed_seconds = 2.5

                progress_callback(_Evt())

    project = StubProject()
    project.settings.know_enabled = True
    project.settings.know = object()
    project.know = _Know()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server.refresh_know_all_with_progress()

    async def _next_progress_packet() -> manager_proto.ProgressPacket:
        while True:
            envelope = await client_endpoint.recv()
            payload = envelope.payload
            if payload.kind == manager_proto.BasePacketKind.PROGRESS:
                assert isinstance(payload, manager_proto.ProgressPacket)
                return payload

    got_update = False
    for _ in range(10):
        payload = await asyncio.wait_for(_next_progress_packet(), timeout=1.0)
        if payload.status is manager_proto.ProgressStatus.UPDATE:
            got_update = True
            assert payload.message is not None
            assert "main" in payload.message
            assert "/tmp/main" in payload.message
            assert "repo_7f2c9a" not in payload.message
            break
    assert got_update is True


@pytest.mark.asyncio
async def test_command_manager_parse_args_with_quotes() -> None:
    manager = CommandManager()
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    received: list[list[str]] = []

    async def handler(srv: UIServer, args: list[str]) -> None:
        received.append(args)

    await manager.register("echo", handler)

    handled = await manager.execute(server, 'echo one "two words" three')

    assert handled is True
    assert received == [["one", "two words", "three"]]


@pytest.mark.asyncio
async def test_command_manager_execute_reports_syntax_error() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.execute(server, 'echo "unterminated')

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "Invalid command syntax" in text


@command("echo2")
@option(0, "text", type=str)
async def _echo2(server: UIServer, text: str) -> None:
    packet = manager_proto.InputPromptPacket(title="echo2", subtitle=text)
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_success() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.execute(server, "echo2 hello")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "echo2"
    assert payload.subtitle == "hello"


@command("need-int")
@option(0, "value", type=int)
async def _need_int(server: UIServer, value: int) -> None:
    packet = manager_proto.InputPromptPacket(title="need-int", subtitle=str(value))
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_validation_error() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.execute(server, "need-int not-an-int")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "Invalid value for 'value' at position 1" in text


@command("splat-echo")
@option(0, "items", type=str, splat=True)
async def _splat_echo(server: UIServer, items: list[str]) -> None:
    joined = ",".join(items)
    packet = manager_proto.InputPromptPacket(title="splat-echo", subtitle=joined)
    await server.send_packet(packet)


@pytest.mark.asyncio
async def test_declarative_command_splat() -> None:
    manager = CommandManager()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=StubProject(), endpoint=server_endpoint)

    handled = await manager.execute(server, "splat-echo one two three")

    assert handled is True
    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.InputPromptPacket)
    assert payload.title == "splat-echo"
    assert payload.subtitle == "one,two,three"


@pytest.mark.asyncio
async def test_uiserver_executes_registered_command() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    received: list[manager_proto.InputPromptPacket] = []

    async def handler(srv: UIServer, args: list[str]) -> None:
        subtitle = " ".join(args)
        packet = manager_proto.InputPromptPacket(title="cmd", subtitle=subtitle)
        await srv.send_packet(packet)

    await server.commands.register("echo", handler)

    message = state.Message(role=models.Role.USER, text="/echo hello")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(payload, manager_proto.InputPromptPacket)
    received.append(payload)

    assert len(received) == 1
    assert received[0].title == "cmd"
    assert received[0].subtitle == "hello"


@pytest.mark.asyncio
async def test_uiserver_unknown_command_sends_error() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/unknown")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    assert payload.text == "Unknown command: /unknown"


@pytest.mark.asyncio
async def test_help_command_lists_debug_and_workflows() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    message = state.Message(role=models.Role.USER, text="/help")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "/debug" in text
    assert "/workflows" in text
    assert "/aa" not in text


@pytest.mark.asyncio
async def test_repo_list_command_outputs_repos() -> None:
    project = StubProject()
    project.settings.know_enabled = True
    project.settings.know = object()
    project.know = _FakeKnowProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/repo list")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    assert "Repos:" in payload.text
    assert "main" in payload.text
    assert "other" in payload.text


@pytest.mark.asyncio
async def test_repo_without_subcommand_prints_help() -> None:
    project = StubProject()
    project.settings.know_enabled = True
    project.settings.know = object()
    project.know = _FakeKnowProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/repo")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    assert "Repository" in payload.text
    assert "/repo list" in payload.text


@pytest.mark.asyncio
async def test_repo_add_command_adds_and_refreshes_repo() -> None:
    project = StubProject()
    project.settings.know_enabled = True
    project.settings.know = object()
    project.know = _FakeKnowProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/repo add new /tmp/new")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert ("new", "/tmp/new") in project.know.pm.added
    assert "new" in project.know.refreshed


@pytest.mark.asyncio
async def test_repo_refresh_all_calls_know_refresh_all() -> None:
    project = StubProject()
    project.settings.know_enabled = True
    project.settings.know = object()
    project.know = _FakeKnowProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    message = state.Message(role=models.Role.USER, text="/repo refresh_all")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert project.know.refreshed_all == 1


@pytest.mark.asyncio
async def test_run_command_unknown_workflow_reports_error() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    message = state.Message(role=models.Role.USER, text="/run missing")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "Unknown workflow 'missing'." in text


@pytest.mark.asyncio
async def test_run_command_stops_all_and_starts_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = vocode_settings.Settings()
    settings.workflows["alpha"] = vocode_settings.WorkflowConfig()
    project = StubProject(settings=settings)

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    called: list[tuple[str, object]] = []

    async def fake_stop_all_runners() -> None:
        called.append(("stop_all", None))

    async def fake_start_workflow(name: str) -> None:
        called.append(("start", name))

    monkeypatch.setattr(server.manager, "stop_all_runners", fake_stop_all_runners)
    monkeypatch.setattr(server.manager, "start_workflow", fake_start_workflow)

    message = state.Message(role=models.Role.USER, text="/run alpha")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert ("stop_all", None) in called
    assert ("start", "alpha") in called


@pytest.mark.asyncio
async def test_reset_command_uses_root_workflow_when_nested_runners_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = vocode_settings.Settings()
    settings.workflows["alpha"] = vocode_settings.WorkflowConfig()
    settings.workflows["beta"] = vocode_settings.WorkflowConfig()
    project = StubProject(settings=settings)
    project.current_workflow = "beta"

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    server.manager._runner_stack = [
        manager_base.RunnerFrame(
            workflow_name="alpha",
            runner=object(),
            initial_message=None,
        ),
        manager_base.RunnerFrame(
            workflow_name="beta",
            runner=object(),
            initial_message=None,
        ),
    ]
    assert [f.workflow_name for f in server.manager.runner_stack] == ["alpha", "beta"]

    called: list[tuple[str, object]] = []

    async def fake_stop_all_runners() -> None:
        called.append(("stop_all", None))

    async def fake_start_workflow(name: str) -> None:
        called.append(("start", name))

    monkeypatch.setattr(server.manager, "stop_all_runners", fake_stop_all_runners)
    monkeypatch.setattr(server.manager, "start_workflow", fake_start_workflow)

    message = state.Message(role=models.Role.USER, text="/reset")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert ("stop_all", None) in called
    assert ("start", "alpha") in called


@pytest.mark.asyncio
async def test_continue_command_calls_manager_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = vocode_settings.Settings()
    settings.workflows["alpha"] = vocode_settings.WorkflowConfig()
    project = StubProject(settings=settings)

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    called: list[object] = []

    async def fake_continue_current_runner() -> None:
        called.append(object())

    monkeypatch.setattr(
        server.manager, "continue_current_runner", fake_continue_current_runner
    )

    message = state.Message(role=models.Role.USER, text="/continue")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert called


@pytest.mark.asyncio
async def test_continue_command_preserves_existing_visible_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, _ = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    node = models.Node(
        name="node-output",
        type="resume-skip",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    workflow = type(
        "_Workflow",
        (),
        {
            "name": "wf-continue-visible-history",
            "graph": models.Graph(nodes=[node], edges=[]),
            "need_input": False,
            "need_input_prompt": None,
        },
    )()
    runner = manager_base.Runner(
        workflow=workflow,
        project=project,
        initial_message=None,
    )
    execution = history.upsert_node_execution(
        runner.execution,
        state.NodeExecution(
            node="node-output",
            status=state.RunStatus.RUNNING,
        ),
    )
    message = state.Message(role=models.Role.ASSISTANT, text="existing-output")
    history.add_message(runner.execution, message)
    output_step = history.upsert_step(
        runner.execution,
        state.Step(
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=message.id,
            is_complete=True,
        ),
    )
    runner.status = state.RunnerStatus.STOPPED

    frame = manager_base.RunnerFrame(
        workflow_name="wf-continue-visible-history",
        runner=runner,
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    await workflow_commands.register_workflow_commands(server.commands)

    before_visible_step_ids = list(runner.execution.step_ids)
    before_all_step_ids = set(runner.execution.steps_by_id.keys())

    message_packet = state.Message(role=models.Role.USER, text="/continue")
    user_packet = manager_proto.UserInputPacket(message=message_packet)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)

    async def wait_for_driver() -> None:
        while True:
            task = server.manager._driver_task
            if task is not None:
                await task
                return
            await asyncio.sleep(0)

    await server.on_ui_packet(envelope)
    await wait_for_driver()

    assert runner.execution.step_ids == before_visible_step_ids
    assert set(runner.execution.steps_by_id.keys()) == before_all_step_ids
    assert runner.execution.step_ids == [output_step.id]


@pytest.mark.asyncio
async def test_continue_command_with_args_reports_usage_error() -> None:
    project = StubProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    message = state.Message(role=models.Role.USER, text="/continue extra")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "Usage: /continue" in text


@pytest.mark.asyncio
async def test_continue_command_reports_manager_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    async def fake_continue_current_runner() -> None:
        raise RuntimeError("No active runner to continue")

    monkeypatch.setattr(
        server.manager, "continue_current_runner", fake_continue_current_runner
    )

    message = state.Message(role=models.Role.USER, text="/continue")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "No active runner to continue" in text


@pytest.mark.asyncio
async def test_reset_command_without_active_workflow_reports_error() -> None:
    project = StubProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    message = state.Message(role=models.Role.USER, text="/reset")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "No active workflow to reset." in text


@pytest.mark.asyncio
async def test_reset_command_with_args_reports_usage_error() -> None:
    project = StubProject()

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    message = state.Message(role=models.Role.USER, text="/reset extra")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    response_envelope = await client_endpoint.recv()
    payload = response_envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.TEXT_MESSAGE
    assert isinstance(payload, manager_proto.TextMessagePacket)
    text = payload.text
    assert "Command error:" in text
    assert "Usage: /reset" in text


@pytest.mark.asyncio
async def test_reset_command_stops_all_and_restarts_current_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = vocode_settings.Settings()
    settings.workflows["alpha"] = vocode_settings.WorkflowConfig()
    project = StubProject(settings=settings)
    project.current_workflow = "alpha"

    server_endpoint, client_endpoint = manager_helpers.InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await workflow_commands.register_workflow_commands(server.commands)

    called: list[tuple[str, object]] = []

    async def fake_stop_all_runners() -> None:
        called.append(("stop_all", None))

    async def fake_start_workflow(name: str) -> None:
        called.append(("start", name))

    monkeypatch.setattr(server.manager, "stop_all_runners", fake_stop_all_runners)
    monkeypatch.setattr(server.manager, "start_workflow", fake_start_workflow)

    message = state.Message(role=models.Role.USER, text="/reset")
    user_packet = manager_proto.UserInputPacket(message=message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=user_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)
    assert handled is True

    assert ("stop_all", None) in called
    assert ("start", "alpha") in called
