from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional
from collections.abc import Awaitable, Callable

from vocode import models, state
from vocode.logger import logger
from vocode.project import Project
from vocode.runner.runner import Runner
from vocode.runner.proto import RunEventReq, RunEventResp, RunEventResponseType
from vocode.runner import proto as runner_proto


class Workflow:
    def __init__(self, name: str, graph: models.Graph) -> None:
        self.name = name
        self.graph = graph


@dataclass
class RunnerFrame:
    workflow_name: str
    runner: Runner
    initial_message: Optional[state.Message]
    task: Optional[asyncio.Task[None]]
    last_stats: Optional[runner_proto.RunStats] = None


RunnerEventListener = Callable[
    [RunnerFrame, RunEventReq],
    Awaitable[Optional[RunEventResp]],
]


class BaseManager:
    def __init__(
        self,
        project: Project,
        run_event_listener: RunnerEventListener,
    ) -> None:
        self.project = project
        self._runner_stack: List[RunnerFrame] = []
        self._started = False
        self._run_event_listener = run_event_listener

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
        await self.stop_all_runners()
        if self._started:
            await self.project.shutdown()
            self._started = False

    async def stop_all_runners(self) -> None:
        frames = list(self._runner_stack)
        for frame in frames:
            frame.runner.stop()
            task = frame.task
            if task is not None:
                task.cancel()

        for frame in frames:
            task = frame.task
            if task is not None:
                try:
                    await task
                except Exception:
                    pass
                frame.task = None

        self._runner_stack.clear()

        self.project.current_workflow = None

    async def start_workflow(
        self,
        workflow_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> Runner:
        workflow = self._build_workflow(workflow_name)
        runner = Runner(workflow, self.project, initial_message)
        task = asyncio.create_task(self._run_runner_task(workflow_name, runner))
        frame = RunnerFrame(
            workflow_name=workflow_name,
            runner=runner,
            initial_message=initial_message,
            task=task,
        )
        self._runner_stack.append(frame)
        self.project.current_workflow = workflow_name

        logger.debug("manager.start_workflow", workflow_name=workflow_name)

        return runner

    async def stop_current_runner(self) -> None:
        if not self._runner_stack:
            return
        frame = self._runner_stack[-1]
        frame.runner.stop()
        task = frame.task
        if task is not None:
            task.cancel()
            try:
                await task
            except Exception:
                pass
            frame.task = None

        logger.debug("manager.stop_current_runner")

    async def continue_current_runner(self) -> Runner:
        if not self._runner_stack:
            raise RuntimeError("No active runner to continue")

        frame = self._runner_stack[-1]
        if frame.task is not None and not frame.task.done():
            raise RuntimeError("Current runner task is already running")

        if frame.runner.status not in (
            state.RunnerStatus.IDLE,
            state.RunnerStatus.STOPPED,
        ):
            raise RuntimeError(
                f"Cannot continue runner in status '{frame.runner.status}'"
            )

        task = asyncio.create_task(
            self._run_runner_task(frame.workflow_name, frame.runner)
        )
        frame.task = task
        self.project.current_workflow = frame.workflow_name

        logger.debug(
            "manager.continue_current_runner", workflow_name=frame.workflow_name
        )

        return frame.runner

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

    async def _emit_run_event(
        self,
        frame: RunnerFrame,
        event: RunEventReq,
    ) -> Optional[RunEventResp]:
        if event.kind == runner_proto.RunEventReqKind.STATUS:
            frame.last_stats = event.stats

        return await self._run_event_listener(frame, event)

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
                send = await self._emit_run_event(frame, event)
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            logger.exception("BaseManager._run_runner_task exception", exc=ex)
            raise
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
        frame.task = None
        if frame.runner.status == state.RunnerStatus.FINISHED:
            self._runner_stack = [f for f in self._runner_stack if f is not frame]

        if self._runner_stack:
            self.project.current_workflow = self._runner_stack[-1].workflow_name
        else:
            self.project.current_workflow = None
