from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional
from collections.abc import Awaitable, Callable, AsyncIterator

from vocode import models, state
from vocode.logger import logger
from vocode.project import Project
from vocode.runner.runner import Runner, RunnerStopped
from vocode.runner.proto import RunEventReq, RunEventResp, RunEventResponseType
from vocode.runner import proto as runner_proto


class Workflow:
    def __init__(
        self,
        name: str,
        graph: models.Graph,
        need_input: bool = False,
        need_input_prompt: Optional[str] = None,
    ) -> None:
        self.name = name
        self.graph = graph
        self.need_input = need_input
        self.need_input_prompt = need_input_prompt


@dataclass
class RunnerFrame:
    workflow_name: str
    runner: Runner
    initial_message: Optional[state.Message]
    agen: Optional[AsyncIterator[RunEventReq]] = None
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
        self._driver_task: Optional[asyncio.Task[None]] = None

    def _ensure_driver_task(self) -> None:
        if self._driver_task is not None and not self._driver_task.done():
            return
        if not self._runner_stack:
            return
        self._driver_task = asyncio.create_task(self._run_runner_task())

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

    async def _stop_runner_agen(self, agen) -> Optional[RunEventReq]:
        if agen is None:
            return None

        try:
            event = await agen.athrow(RunnerStopped())
        except StopAsyncIteration:
            return None

        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass

        return event

    async def stop_all_runners(self) -> None:
        frames = list(self._runner_stack)

        task = self._driver_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass

        self._driver_task = None

        for frame in frames:
            await self._stop_runner_agen(frame.agen)
            frame.agen = None

        self._runner_stack.clear()
        self.project.current_workflow = None

    async def start_workflow(
        self,
        workflow_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> Runner:
        workflow = self._build_workflow(workflow_name)
        runner = Runner(workflow, self.project, initial_message)
        frame = RunnerFrame(
            workflow_name=workflow_name,
            runner=runner,
            initial_message=initial_message,
        )
        self._runner_stack.append(frame)
        self.project.current_workflow = workflow_name

        self._ensure_driver_task()
        return runner

    async def stop_current_runner(self) -> None:
        task = self._driver_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception as ex:
                pass
            self._driver_task = None

    async def continue_current_runner(self) -> Runner:
        if not self._runner_stack:
            raise RuntimeError("No active runner to continue")

        frame = self._runner_stack[-1]
        if frame.runner.status not in (
            state.RunnerStatus.IDLE,
            state.RunnerStatus.STOPPED,
        ):
            raise RuntimeError(
                f"Cannot continue runner in status '{frame.runner.status}'"
            )
        self.project.current_workflow = frame.workflow_name

        self._ensure_driver_task()
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
        return Workflow(
            name=name,
            graph=graph,
            need_input=wf.need_input,
            need_input_prompt=wf.need_input_prompt,
        )

    async def _run_runner_task(self) -> None:
        try:
            send: Optional[RunEventResp] = None
            while self._runner_stack:
                # Get current runner from the stack and iterate on it
                frame = self._runner_stack[-1]

                if frame.agen is None:
                    frame.agen = frame.runner.run()

                agen = frame.agen

                try:
                    if send is None:
                        event = await agen.__anext__()
                    else:
                        event = await agen.asend(send)
                except StopAsyncIteration:
                    # If runner generator finished, pop it from the stack
                    finished_frame = self._runner_stack.pop()
                    finished_frame.agen = None

                    # If we still have the runners in the stack, then pass back last final message
                    final_message = finished_frame.runner.last_final_message

                    if self._runner_stack:
                        parent_frame = self._runner_stack[-1]
                        if final_message is not None:
                            send = RunEventResp(
                                resp_type=RunEventResponseType.MESSAGE,
                                message=final_message,
                            )
                        else:
                            send = RunEventResp(
                                resp_type=RunEventResponseType.NOOP,
                                message=None,
                            )
                        self.project.current_workflow = parent_frame.workflow_name
                    else:
                        send = None
                        self.project.current_workflow = None

                    continue

                send = await self._emit_run_event(frame, event)
        except asyncio.CancelledError:
            # Runner task was canceled, cleanup by canceling runner generator
            if self._runner_stack:
                frame = self._runner_stack[-1]

                event = await self._stop_runner_agen(frame.agen)
                if event is None:
                    # Forcibly set status to stopped because generator did not exit cleanly
                    event = frame.runner.set_status(
                        state.RunnerStatus.STOPPED,
                        current_execution=None,
                    )

                # Emit final event (would be the status)
                try:
                    send = await self._emit_run_event(frame, event)
                except Exception as ex:
                    logger.exception(
                        "BaseManager._run_runner_task stop emit exception",
                        exc=ex,
                    )
                    send = None

                frame.agen = None
        except Exception as ex:
            logger.exception("BaseManager._run_runner_task exception", exc=ex)
            raise
        finally:
            self._driver_task = None
            if not self._runner_stack:
                self.project.current_workflow = None
