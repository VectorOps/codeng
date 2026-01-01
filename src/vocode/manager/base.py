from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Awaitable, Callable

from vocode import models, state
from vocode.project import Project
from vocode.runner.runner import Runner
from vocode.runner.proto import RunEventReq, RunEventResp, RunEventResponseType


class Workflow:
    def __init__(self, name: str, graph: models.Graph) -> None:
        self.name = name
        self.graph = graph


@dataclass
class RunnerFrame:
    workflow_name: str
    runner: Runner
    initial_message: Optional[state.Message]
    task: asyncio.Task[None]


class BaseManager:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._runner_stack: List[RunnerFrame] = []
        self._started = False

    @property
    def runner_stack(self) -> List[RunnerFrame]:
        return list(self._runner_stack)

    @property
    def current_runner(self) -> Optional[Runner]:
        if not self._runner_stack:
            return None
        return self._runner_stack[-1].runner

    async def start(self) -> None:
        if self._started:
            return
        await self.project.start()
        self._started = True

    async def stop(self) -> None:
        for frame in list(self._runner_stack):
            frame.runner.stop()
        for frame in list(self._runner_stack):
            try:
                await frame.task
            except Exception:
                pass
        self._runner_stack.clear()
        self.project.current_workflow = None
        if self._started:
            await self.project.shutdown()
            self._started = False

    async def start_workflow(
        self,
        workflow_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> Runner:
        workflow = self._build_workflow(workflow_name)
        runner = Runner(workflow, self.project, initial_message)
        task = asyncio.create_task(
            self._run_runner_task(workflow_name, runner, initial_message)
        )
        frame = RunnerFrame(
            workflow_name=workflow_name,
            runner=runner,
            initial_message=initial_message,
            task=task,
        )
        self._runner_stack.append(frame)
        self.project.current_workflow = workflow_name
        return runner

    async def stop_current_runner(self) -> None:
        if not self._runner_stack:
            return
        frame = self._runner_stack[-1]
        frame.runner.stop()
        try:
            await frame.task
        except Exception:
            pass

    async def restart_current_runner(
        self,
        initial_message: Optional[state.Message] = None,
    ) -> Runner:
        if not self._runner_stack:
            raise RuntimeError("No active runner to restart")
        frame = self._runner_stack[-1]
        workflow_name = frame.workflow_name
        message = (
            initial_message if initial_message is not None else frame.initial_message
        )
        await self.stop_current_runner()
        return await self.start_workflow(workflow_name, message)

    async def on_run_event(
        self,
        frame: RunnerFrame,
        event: RunEventReq,
    ) -> Optional[RunEventResp]:
        return RunEventResp(resp_type=RunEventResponseType.NOOP, message=None)

    def _build_workflow(self, workflow_name: str) -> Workflow:
        settings = self.project.settings
        if settings is None:
            raise RuntimeError("Project settings are not available")
        wf = settings.workflows.get(workflow_name)
        if wf is None:
            raise KeyError(f"Unknown workflow '{workflow_name}'")
        name = wf.name or workflow_name
        graph = models.Graph(nodes=wf.nodes, edges=wf.edges)
        return Workflow(name=name, graph=graph)

    async def _run_runner_task(
        self,
        workflow_name: str,
        runner: Runner,
        initial_message: Optional[state.Message],
    ) -> None:
        frame = self._find_frame(workflow_name, runner)
        try:
            agen = runner.run()
            send: Optional[RunEventResp] = None
            while True:
                try:
                    if send is None:
                        event = await agen.__anext__()
                    else:
                        event = await agen.asend(send)
                except StopAsyncIteration:
                    break
                try:
                    send = await self.on_run_event(frame, event)
                except Exception:
                    send = RunEventResp(
                        resp_type=RunEventResponseType.NOOP, message=None
                    )
        finally:
            self._on_runner_finished(frame)

    def _find_frame(self, workflow_name: str, runner: Runner) -> RunnerFrame:
        for frame in self._runner_stack:
            if frame.workflow_name == workflow_name and frame.runner is runner:
                return frame
        dummy_task = asyncio.create_task(asyncio.sleep(0))
        return RunnerFrame(
            workflow_name=workflow_name,
            runner=runner,
            initial_message=None,
            task=dummy_task,
        )

    def _on_runner_finished(self, frame: RunnerFrame) -> None:
        self._runner_stack = [f for f in self._runner_stack if f is not frame]
        if self._runner_stack:
            self.project.current_workflow = self._runner_stack[-1].workflow_name
        else:
            self.project.current_workflow = None
