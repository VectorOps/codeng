from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from vocode import input_manager, models, state
from vocode.runner import proto as runner_proto

from . import proto as manager_proto
from .base import RunnerFrame
from .interfaces import UIManager, UIPacketSender


class RunnerEventController:
    def __init__(
        self,
        *,
        manager: UIManager,
        packet_sender: UIPacketSender,
        publish_workflow_start_error: Callable[[str, Exception], Awaitable[None]],
        status: manager_proto.UIServerStatus,
    ) -> None:
        self._manager = manager
        self._packet_sender = packet_sender
        self._publish_workflow_start_error = publish_workflow_start_error
        self._status = status

    @property
    def status(self) -> manager_proto.UIServerStatus:
        return self._status

    @status.setter
    def status(self, value: manager_proto.UIServerStatus) -> None:
        self._status = value

    async def handle(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        if event.kind == runner_proto.RunEventReqKind.STATUS:
            return await self._handle_status(frame, event)

        if event.kind == runner_proto.RunEventReqKind.START_WORKFLOW:
            return await self._handle_start_workflow(frame, event)

        return await self._handle_step(frame, event)

    async def _handle_step(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        execution = event.execution
        step = event.step
        if step is None:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP,
                message=None,
            )

        message = step.message

        display: Optional[manager_proto.RunnerReqDisplayOpts] = None
        node_name = step.execution.node
        node_by_name = frame.runner.workflow.graph.node_by_name
        node = node_by_name.get(node_name)
        if node is not None and (
            node.alert
            or node.collapse is not None
            or node.collapse_lines is not None
            or not node.visible
            or node.tool_collapse is not None
        ):
            display = manager_proto.RunnerReqDisplayOpts(
                collapse=node.collapse,
                collapse_lines=node.collapse_lines,
                visible=node.visible,
                tool_collapse=node.tool_collapse,
                alert=node.alert,
            )

        input_title: Optional[str] = None
        input_subtitle: Optional[str] = None

        needs_confirmation = False
        if step.type == state.StepType.TOOL_REQUEST and message is not None:
            for tool_req in message.tool_call_requests:
                if tool_req.status == state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
                    needs_confirmation = True
                    break

        input_required = False
        if step.type in (state.StepType.PROMPT, state.StepType.PROMPT_CONFIRM):
            input_required = True
            if step.type == state.StepType.PROMPT_CONFIRM:
                input_title = "Press enter to confirm or provide a reply"
            else:
                input_title = "Input"
        elif step.type == state.StepType.TOOL_REQUEST and needs_confirmation:
            input_required = True
            input_title = "Please confirm the tool call"
            input_subtitle = (
                "Empty line confirms, any text to reject with a message. "
                "Tip: type /aa to auto-approve similar calls for this session"
            )

        await self._packet_sender.send_packet(
            manager_proto.RunnerReqPacket(
                workflow_id=frame.workflow_name,
                workflow_name=execution.workflow_name,
                workflow_execution_id=str(execution.id),
                step=step,
                input_required=input_required,
                display=display,
            )
        )

        if not input_required:
            if step.type == state.StepType.TOOL_REQUEST and not needs_confirmation:
                return runner_proto.RunEventResp(
                    resp_type=runner_proto.RunEventResponseType.APPROVE,
                    message=None,
                )

            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.NOOP,
                message=None,
            )

        await self._packet_sender.send_packet(
            manager_proto.InputPromptPacket(
                title=input_title,
                subtitle=input_subtitle,
            )
        )

        return runner_proto.RunEventResp(
            resp_type=runner_proto.RunEventResponseType.NOOP,
            message=None,
        )

    async def _handle_status(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        _ = frame
        stats = event.stats
        if stats is not None and stats.status in (
            state.RunnerStatus.STOPPED,
            state.RunnerStatus.FINISHED,
        ):
            await self._packet_sender.send_packet(manager_proto.InputPromptPacket())

        runners: list[manager_proto.RunnerStackFrame] = []
        active_node_started_at = None
        last_user_input_at = None
        active_workflow_usage: Optional[state.LLMUsageStats] = None
        last_step_usage: Optional[state.LLMUsageStats] = None
        for runner_frame in self._manager.runner_stack:
            runner_stats = runner_frame.last_stats
            if runner_stats is None:
                continue
            execution = runner_frame.runner.execution
            node_name = ""
            node_execution_id = None
            node_started_at = None
            stats_execution_id = runner_stats.current_node_execution_id
            if stats_execution_id is not None:
                node_execution = execution.node_executions.get(stats_execution_id)
                if node_execution is not None:
                    if node_execution.step_ids:
                        first_step = execution.get_step(node_execution.step_ids[0])
                        node_started_at = first_step.created_at
                    node_name = node_execution.node
                    node_execution_id = str(node_execution.id)
            runners.append(
                manager_proto.RunnerStackFrame(
                    workflow_name=execution.workflow_name,
                    workflow_execution_id=str(execution.id),
                    node_name=node_name,
                    node_execution_id=node_execution_id,
                    status=runner_stats.status,
                )
            )
            if node_started_at is not None:
                active_node_started_at = node_started_at
            if execution.last_user_input_at is not None:
                last_user_input_at = execution.last_user_input_at
            if execution.llm_usage is not None:
                active_workflow_usage = execution.llm_usage
            if execution.last_step_llm_usage is not None:
                last_step_usage = execution.last_step_llm_usage

        input_snapshot = await self._manager.project.input_manager.snapshot()
        queued_steering_count = 0
        queued_steering_preview = None
        queued_messages = list(
            input_snapshot.queued_messages_by_type.get(
                input_manager.INPUT_TYPE_INTERACTIVE,
                (),
            )
        )
        steering_messages = [
            message
            for message in queued_messages
            if message.input_mode == state.UserInputMode.STEERING
        ]
        if steering_messages:
            queued_steering_count = len(steering_messages)
            queued_steering_preview = steering_messages[0].text.strip()
            if not queued_steering_preview:
                queued_steering_preview = None
            elif len(queued_steering_preview) > 48:
                queued_steering_preview = f"{queued_steering_preview[:45]}..."

        await self._packet_sender.send_packet(
            manager_proto.UIServerStatePacket(
                status=self._status,
                runners=runners,
                active_node_started_at=active_node_started_at,
                last_user_input_at=last_user_input_at,
                queued_steering_count=queued_steering_count,
                queued_steering_preview=queued_steering_preview,
                active_workflow_llm_usage=active_workflow_usage,
                last_step_llm_usage=last_step_usage,
                project_llm_usage=self._manager.project.llm_usage,
            )
        )
        return runner_proto.RunEventResp(
            resp_type=runner_proto.RunEventResponseType.NOOP,
            message=None,
        )

    async def _handle_start_workflow(
        self,
        frame: RunnerFrame,
        event: runner_proto.RunEventReq,
    ) -> Optional[runner_proto.RunEventResp]:
        _ = frame
        payload = event.start_workflow
        if payload is None:
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.MESSAGE,
                message=state.Message(
                    role=models.Role.SYSTEM,
                    text="Start-workflow event is missing payload.",
                ),
            )
        try:
            await self._manager.start_workflow(
                payload.workflow_name,
                initial_message=payload.initial_message,
            )
        except Exception as ex:
            await self._publish_workflow_start_error(payload.workflow_name, ex)
            return runner_proto.RunEventResp(
                resp_type=runner_proto.RunEventResponseType.MESSAGE,
                message=state.Message(
                    role=models.Role.SYSTEM,
                    text=f"Failed to start workflow '{payload.workflow_name}': {ex}",
                ),
            )

        return None
