from typing import Optional, Dict, AsyncIterator

from vocode import models, state
from vocode import settings as vocode_settings
from vocode.tools import base as tools_base
from vocode.lib.date import utcnow
from vocode.logger import logger
from vocode.project import Project
from vocode.graph import RuntimeGraph
from vocode.lib import message_helpers, validators
from .base import BaseExecutor, ExecutorFactory, ExecutorInput
from .proto import RunEventReq, RunEventResp, RunEventResponseType
from . import proto as runner_proto


RunEvent = RunEventReq


class RunnerStopped(Exception):
    pass


class Runner:
    def __init__(
        self,
        workflow,
        project: Project,
        initial_message: Optional[state.Message],
    ):
        self.workflow = workflow
        self.project = project
        self.initial_message = initial_message
        self.need_input = bool(workflow.need_input)
        self.need_input_prompt = workflow.need_input_prompt

        self.status = state.RunnerStatus.IDLE
        self.graph = RuntimeGraph(workflow.graph)
        self.execution = state.WorkflowExecution(workflow_name=workflow.name)
        self._last_final_message: Optional[state.Message] = None
        self._last_final_message: Optional[state.Message] = None

        self._executors: Dict[str, BaseExecutor] = {
            n.name: ExecutorFactory.create_for_node(n, project=self.project)
            for n in self.workflow.graph.nodes
        }
        self.project.state_manager.track(self.execution)

    def _touch_execution(self) -> None:
        self.execution.touch()
        self.project.state_manager.notify_changed(self.execution)

    def _set_node_execution_status(
        self,
        execution: state.NodeExecution,
        status: state.RunStatus,
    ) -> None:
        execution.status = status
        self._touch_execution()

    @property
    def last_final_message(self) -> Optional[state.Message]:
        return self._last_final_message

    # Tool calling
    def _get_tool_spec_for_request(
        self, req: state.ToolCallReq
    ) -> Optional[vocode_settings.ToolSpec]:
        if req.tool_spec is not None:
            return req.tool_spec
        project_settings = self.project.settings
        if project_settings is None:
            return None
        for spec in project_settings.tools:
            if spec.name == req.name:
                return spec
        return None

    def _is_tool_call_auto_approved(self, req: state.ToolCallReq) -> bool:
        spec = self._get_tool_spec_for_request(req)
        if spec is None:
            return False
        if spec.auto_approve is True:
            return True
        if spec.auto_approve_rules and validators.tool_auto_approve_matches(
            spec.auto_approve_rules, req.arguments
        ):
            return True
        return False

    async def _execute_tool_call(
        self, req: state.ToolCallReq
    ) -> runner_proto.ToolExecResult:
        spec = self._get_tool_spec_for_request(req)
        tools = self.project.tools
        tool = tools.get(req.name)
        if tool is None:
            resp = state.ToolCallResp(
                id=req.id,
                status=state.ToolCallStatus.FAILED,
                name=req.name,
                result={"error": f"Tool '{req.name}' is not available."},
            )
            return runner_proto.ToolExecResponse(response=resp)
        if spec is None:
            spec = vocode_settings.ToolSpec(name=req.name)
        try:
            tool_req = tools_base.ToolReq(execution=self.execution, spec=spec)
            raw_result = await tool.run(tool_req, req.arguments)
        except RunnerStopped:
            raise
        except Exception as exc:
            # TODO: send exception back?
            resp = state.ToolCallResp(
                id=req.id,
                status=state.ToolCallStatus.FAILED,
                name=req.name,
                result={"error": str(exc)},
            )
            return runner_proto.ToolExecResponse(response=resp)

        if raw_result is None:
            resp = state.ToolCallResp(
                id=req.id,
                status=state.ToolCallStatus.COMPLETED,
                name=req.name,
                result=None,
            )
            return runner_proto.ToolExecResponse(response=resp)

        if raw_result.type == tools_base.ToolResponseType.start_workflow:
            workflow_name = raw_result.workflow
            initial_message = raw_result.initial_message
            if initial_message is None and raw_result.initial_text is not None:
                initial_message = state.Message(
                    role=models.Role.USER,
                    text=raw_result.initial_text,
                )
            return runner_proto.ToolExecStartWorkflow(
                workflow_name=workflow_name,
                initial_text=raw_result.initial_text,
                initial_message=initial_message,
            )

        result_data = raw_result.model_dump()
        resp = state.ToolCallResp(
            id=req.id,
            status=state.ToolCallStatus.COMPLETED,
            name=req.name,
            result=result_data,
        )
        return runner_proto.ToolExecResponse(response=resp)

    def _create_tool_prompt_step(
        self,
        execution: state.NodeExecution,
        req: state.ToolCallReq,
        usage: Optional[state.LLMUsageStats] = None,
    ) -> state.Step:
        prompt_message = state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        )
        prompt_step = state.Step(
            execution=execution,
            type=state.StepType.TOOL_REQUEST,
            message=prompt_message,
            is_complete=True,
            is_final=False,
        )
        if usage is not None:
            prompt_step.llm_usage = state.LLMUsageStats(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_dollars=usage.cost_dollars,
                input_token_limit=usage.input_token_limit,
                output_token_limit=usage.output_token_limit,
            )
        return self._persist_step(prompt_step)

    def _process_tool_approval_response(
        self,
        req: state.ToolCallReq,
        response_step: Optional[state.Step],
        approved: list[state.ToolCallReq],
        tool_responses: list[state.ToolCallResp],
    ) -> bool:
        if response_step is None:
            return False
        if response_step.type == state.StepType.APPROVAL:
            req.status = state.ToolCallReqStatus.PENDING_EXECUTION
            approved.append(req)
            return True
        if response_step.type == state.StepType.REJECTION:
            parts: list[str] = ["A user rejected the tool call."]
            if response_step.message is not None:
                text = response_step.message.text.strip()
                if text:
                    parts.append(text)
            rejection_text = " ".join(parts)
            tool_responses.append(
                state.ToolCallResp(
                    id=req.id,
                    status=state.ToolCallStatus.REJECTED,
                    name=req.name,
                    result={"message": rejection_text},
                )
            )
            req.status = state.ToolCallReqStatus.REJECTED
            return True
        return False

    async def _execute_approved_tool_calls(
        self,
        approved: list[state.ToolCallReq],
    ) -> list[runner_proto.ToolExecResult]:
        results: list[runner_proto.ToolExecResult] = []
        for req in approved:
            result = await self._execute_tool_call(req)
            results.append(result)
        return results

    def _create_transition_error_event(
        self,
        execution: state.NodeExecution,
        text: str,
    ) -> RunEventReq:
        error_message = state.Message(
            role=models.Role.SYSTEM,
            text=text,
        )
        error_step = state.Step(
            execution=execution,
            type=state.StepType.REJECTION,
            message=error_message,
        )
        persisted_error = self._persist_step(error_step)
        return RunEventReq(
            kind=runner_proto.RunEventReqKind.STEP,
            execution=self.execution,
            step=persisted_error,
        )

    # History management
    def _persist_step(self, step: state.Step) -> state.Step:
        execution = step.execution
        if execution.id not in self.execution.node_executions:
            self.execution.node_executions[execution.id] = execution

        node_execution = self.execution.node_executions[execution.id]

        node_steps = node_execution.steps
        existing_index = None
        for i in range(len(node_steps) - 1, -1, -1):
            if node_steps[i].id == step.id:
                existing_index = i
                break
        if existing_index is not None:
            node_steps[existing_index] = step
        else:
            node_steps.append(step)

        run_steps = self.execution.steps
        existing_run_index = None
        for i in range(len(run_steps) - 1, -1, -1):
            if run_steps[i].id == step.id:
                existing_run_index = i
                break
        if existing_run_index is not None:
            run_steps[existing_run_index] = step
        else:
            run_steps.append(step)
        if (
            step.type == state.StepType.INPUT_MESSAGE
            and step.message is not None
            and step.message.role == models.Role.USER
        ):
            self.execution.last_user_input_at = step.created_at
        self._touch_execution()
        return step

    def _build_next_input_messages(
        self,
        execution: state.NodeExecution,
        node_model: models.Node,
    ) -> list[state.Message]:
        mode = node_model.message_mode

        if mode == models.ResultMode.ALL_MESSAGES:
            messages: list[state.Message] = []
            messages.extend(execution.input_messages)
            for s in execution.steps:
                if s.message is None:
                    continue
                if s.type not in (
                    state.StepType.OUTPUT_MESSAGE,
                    state.StepType.INPUT_MESSAGE,
                ):
                    continue
                messages.append(s.message)
            return messages

        final_message: Optional[state.Message] = None
        for s in reversed(execution.steps):
            if s.type == state.StepType.OUTPUT_MESSAGE and s.message is not None:
                final_message = s.message
                break

        if mode == models.ResultMode.FINAL_RESPONSE:
            if final_message is None:
                return []
            user_message = state.Message(
                role=models.Role.USER,
                text=final_message.text,
            )
            return [user_message]

        if mode == models.ResultMode.CONCATENATE_FINAL:
            merged_messages: list[state.Message] = []
            if execution.input_messages:
                merged_messages.extend(execution.input_messages)
            if final_message is not None:
                merged_messages.append(final_message)

            combined_message = message_helpers.concatenate_messages(
                merged_messages,
                tool_message=final_message,
                default_role=models.Role.USER,
            )
            if combined_message is None:
                return []
            user_combined = state.Message(
                role=models.Role.USER,
                text=combined_message.text,
            )
            return [user_combined]

        return []

    def _find_node_execution(
        self,
        node_name: str,
    ) -> Optional[state.NodeExecution]:
        latest: Optional[state.NodeExecution] = None
        for execution in self.execution.node_executions.values():
            if execution.node != node_name:
                continue
            if latest is None or execution.created_at > latest.created_at:
                latest = execution
        return latest

    def _create_node_execution(
        self,
        node_name: str,
        input_messages: Optional[list[state.Message]] = None,
        previous_execution: Optional[state.NodeExecution] = None,
    ) -> state.NodeExecution:
        effective_input_messages: list[state.Message] = []
        if input_messages is not None:
            effective_input_messages.extend(input_messages)
        execution = state.NodeExecution(
            node=node_name,
            input_messages=effective_input_messages,
            previous=previous_execution,
            status=state.RunStatus.RUNNING,
        )
        self.execution.node_executions[execution.id] = execution
        self._touch_execution()
        return execution

    def _compute_resume_state(
        self,
    ) -> tuple[
        Optional[object],
        Optional[state.NodeExecution],
        Optional[state.Step],
        bool,
    ]:
        resume_step: Optional[state.Step] = None
        skip_executor = False
        changed = False
        if self.execution.steps:
            anchor_index: Optional[int] = None
            for i in range(len(self.execution.steps) - 1, -1, -1):
                step = self.execution.steps[i]
                if not step.is_complete:
                    continue
                if step.type not in (
                    state.StepType.OUTPUT_MESSAGE,
                    state.StepType.INPUT_MESSAGE,
                ):
                    continue
                anchor_index = i
                resume_step = step
                break
            if anchor_index is None:
                ids = [s.id for s in self.execution.steps]
                if ids:
                    self.execution.delete_steps(ids)
                    changed = True
                resume_step = None
            else:
                tail_ids = [s.id for s in self.execution.steps[anchor_index + 1 :]]
                if tail_ids:
                    self.execution.delete_steps(tail_ids)
                    changed = True
                if (
                    resume_step is not None
                    and resume_step.type == state.StepType.OUTPUT_MESSAGE
                ):
                    skip_executor = True
            self.execution.trim_empty_node_executions()
            if changed:
                self._touch_execution()

        if not self.execution.steps:
            runtime_node = self.graph.root
            current_execution: Optional[state.NodeExecution] = None
            if runtime_node is not None:
                existing_execution = self._find_node_execution(runtime_node.name)
                if existing_execution is not None:
                    self._set_node_execution_status(
                        existing_execution, state.RunStatus.RUNNING
                    )
                    current_execution = existing_execution
                else:
                    initial_messages: list[state.Message] = []
                    if self.initial_message is not None:
                        initial_messages.append(self.initial_message)
                    current_execution = self._create_node_execution(
                        runtime_node.name,
                        input_messages=initial_messages,
                    )
            return runtime_node, current_execution, None, False

        last_step = self.execution.steps[-1]
        execution_id = last_step.execution.id
        execution = self.execution.node_executions.get(
            execution_id, last_step.execution
        )
        runtime_node = self.graph.get_runtime_node_by_name(execution.node)
        if execution.status != state.RunStatus.RUNNING:
            self._set_node_execution_status(execution, state.RunStatus.RUNNING)
        return runtime_node, execution, resume_step, skip_executor

    def _handle_run_event_response(
        self, req: RunEventReq, resp: Optional[RunEventResp]
    ) -> Optional[state.Step]:
        if resp is None:
            return None
        if resp.resp_type == RunEventResponseType.NOOP:
            return None
        base_execution = req.step.execution
        message = resp.message
        if resp.resp_type == RunEventResponseType.APPROVE:
            step_type = state.StepType.APPROVAL
        elif resp.resp_type == RunEventResponseType.DECLINE:
            step_type = state.StepType.REJECTION
        elif resp.resp_type == RunEventResponseType.MESSAGE:
            step_type = state.StepType.INPUT_MESSAGE
        else:
            return None
        step = state.Step(
            execution=base_execution,
            type=step_type,
            message=message,
        )
        persisted = self._persist_step(step)
        return persisted

    def _build_response_event(
        self, response_step: Optional[state.Step]
    ) -> Optional[RunEventReq]:
        if response_step is None:
            return None
        return RunEventReq(
            kind=runner_proto.RunEventReqKind.STEP,
            execution=self.execution,
            step=response_step,
        )

    async def _init_executors(self) -> None:
        for executor in self._executors.values():
            await executor.init()

    async def _shutdown_executors(self) -> None:
        for executor in self._executors.values():
            await executor.shutdown()

    # Main runner loop
    async def run(self) -> AsyncIterator[RunEventReq]:
        if self.status not in (state.RunnerStatus.IDLE, state.RunnerStatus.STOPPED):
            raise RuntimeError(
                f"run() not allowed when runner status is '{self.status}'. Allowed: 'idle', 'stopped'"
            )

        current_execution: Optional[state.NodeExecution]
        await self._init_executors()

        try:
            (
                runtime_node,
                current_execution,
                resume_step,
                skip_executor,
            ) = self._compute_resume_state()

            if runtime_node is None:
                status_event = self.set_status(
                    state.RunnerStatus.FINISHED,
                    current_execution=None,
                )
                _ = yield status_event
                return

            if (
                self.need_input
                and self.initial_message is None
                and not self.execution.steps
                and current_execution is not None
            ):
                waiting_event = self.set_status(
                    state.RunnerStatus.WAITING_INPUT,
                    current_execution=current_execution,
                )
                _ = yield waiting_event

                prompt_text = self.need_input_prompt or "What are we doing today?"
                prompt_message = state.Message(
                    role=models.Role.ASSISTANT,
                    text=prompt_text,
                )
                prompt_step = state.Step(
                    execution=current_execution,
                    type=state.StepType.PROMPT,
                    message=prompt_message,
                    is_complete=True,
                )
                persisted_prompt = self._persist_step(prompt_step)
                req = RunEventReq(
                    kind=runner_proto.RunEventReqKind.STEP,
                    execution=self.execution,
                    step=persisted_prompt,
                )
                resp = yield req
                response_step = self._handle_run_event_response(req, resp)
                response_event = self._build_response_event(response_step)
                if response_event is not None:
                    _ = yield response_event

            status_event = self.set_status(
                state.RunnerStatus.RUNNING,
                current_execution=current_execution,
            )
            _ = yield status_event
            current_runtime_node = runtime_node
            use_resume_step = resume_step
            skip_executor_for_current = skip_executor

            tool_request_steps: dict[int, state.Step] = {}

            while True:
                executor = self._executors[current_runtime_node.name]
                executor_input = ExecutorInput(
                    execution=current_execution, run=self.execution
                )

                last_complete_step: Optional[state.Step] = None
                complete_step_count = 0
                if skip_executor_for_current and use_resume_step is not None:
                    last_complete_step = use_resume_step
                    complete_step_count = 1
                    skip_executor_for_current = False
                    use_resume_step = None
                else:
                    async for step in executor.run(executor_input):
                        node_output_mode = current_runtime_node.model.output_mode
                        if step.output_mode != node_output_mode:
                            step.output_mode = node_output_mode

                        persisted_step = self._persist_step(step)

                        if step.type in (
                            state.StepType.PROMPT,
                            state.StepType.PROMPT_CONFIRM,
                        ):
                            waiting_event = self.set_status(
                                state.RunnerStatus.WAITING_INPUT,
                                current_execution=current_execution,
                            )
                            _ = yield waiting_event

                        req = RunEventReq(
                            kind=runner_proto.RunEventReqKind.STEP,
                            execution=self.execution,
                            step=persisted_step,
                        )
                        resp = yield req

                        response_step = self._handle_run_event_response(req, resp)
                        response_event = self._build_response_event(response_step)
                        if response_event is not None:
                            _ = yield response_event

                        if persisted_step.type in (
                            state.StepType.PROMPT,
                            state.StepType.PROMPT_CONFIRM,
                        ):
                            resume_status_event = self._set_running_after_input(
                                current_execution
                            )
                            if resume_status_event is not None:
                                _ = yield resume_status_event
                        if persisted_step.is_complete:
                            complete_step_count += 1
                            last_complete_step = persisted_step

                if complete_step_count == 0:
                    raise RuntimeError(
                        "Executor finished without yielding a complete step for the node run."
                    )
                if complete_step_count > 1:
                    raise RuntimeError(
                        "Executor yielded more than one complete step for a single run."
                    )

                self._apply_llm_usage(last_complete_step.llm_usage)

                if last_complete_step.llm_usage is not None:
                    usage_status_event = self.set_status(
                        self.status,
                        current_execution=current_execution,
                    )
                    _ = yield usage_status_event

                if (
                    last_complete_step is not None
                    and last_complete_step.type == state.StepType.WORKFLOW_REQUEST
                ):
                    workflow_name = getattr(current_runtime_node.model, "workflow", "")
                    start_payload = runner_proto.RunEventStartWorkflow(
                        workflow_name=workflow_name,
                        initial_message=last_complete_step.message,
                    )
                    req = RunEventReq(
                        kind=runner_proto.RunEventReqKind.START_WORKFLOW,
                        execution=self.execution,
                        start_workflow=start_payload,
                    )
                    resp = yield req

                    if resp and resp.message:
                        result_step = state.Step(
                            execution=current_execution,
                            type=state.StepType.WORKFLOW_RESULT,
                            message=resp.message,
                            is_complete=True,
                        )
                        self._persist_step(result_step)
                        continue

                if last_complete_step is not None and last_complete_step.type in (
                    state.StepType.PROMPT,
                    state.StepType.PROMPT_CONFIRM,
                ):
                    continue

                msg = last_complete_step.message
                if msg is not None and msg.tool_call_requests:
                    approved: list[state.ToolCallReq] = []
                    tool_responses: list[state.ToolCallResp] = []

                    for req in msg.tool_call_requests:
                        is_auto_approved = self._is_tool_call_auto_approved(req)
                        if is_auto_approved:
                            req.auto_approved = True
                            req.status = state.ToolCallReqStatus.PENDING_EXECUTION
                        else:
                            req.status = state.ToolCallReqStatus.REQUIRES_CONFIRMATION

                        while True:
                            persisted_prompt = self._create_tool_prompt_step(
                                current_execution,
                                req,
                                last_complete_step.llm_usage
                                if last_complete_step is not None
                                else None,
                            )
                            tool_request_steps[id(req)] = persisted_prompt

                            if not is_auto_approved:
                                waiting_event = self.set_status(
                                    state.RunnerStatus.WAITING_INPUT,
                                    current_execution=current_execution,
                                )
                                _ = yield waiting_event

                            req_event = RunEventReq(
                                kind=runner_proto.RunEventReqKind.STEP,
                                execution=self.execution,
                                step=persisted_prompt,
                            )
                            resp_event = yield req_event

                            if is_auto_approved:
                                approved.append(req)
                                break

                            response_step = self._handle_run_event_response(
                                req_event, resp_event
                            )
                            response_event = self._build_response_event(response_step)
                            if response_event is not None:
                                _ = yield response_event

                            before_len = len(approved)
                            if not is_auto_approved:
                                resume_status_event = self._set_running_after_input(
                                    current_execution
                                )
                                if resume_status_event is not None:
                                    _ = yield resume_status_event
                            handled = self._process_tool_approval_response(
                                req,
                                response_step,
                                approved,
                                tool_responses,
                            )
                            if handled:
                                if len(approved) > before_len:
                                    break
                                persisted_prompt.is_final = True
                                break

                    if approved:
                        for req in approved:
                            tool_step = tool_request_steps.get(id(req))
                            if tool_step is None:
                                continue
                            req.status = state.ToolCallReqStatus.EXECUTING
                            status_event = RunEventReq(
                                kind=runner_proto.RunEventReqKind.STEP,
                                execution=self.execution,
                                step=tool_step,
                            )
                            _ = yield status_event

                        exec_results = await self._execute_approved_tool_calls(approved)
                        for exec_result in exec_results:
                            if (
                                exec_result.kind
                                == runner_proto.ToolExecResultKind.RESPONSE
                            ):
                                tool_responses.append(exec_result.response)  # type: ignore[attr-defined]
                            elif (
                                exec_result.kind
                                == runner_proto.ToolExecResultKind.START_WORKFLOW
                            ):
                                start_payload = runner_proto.RunEventStartWorkflow(
                                    workflow_name=exec_result.workflow_name,  # type: ignore[attr-defined]
                                    initial_message=exec_result.initial_message,  # type: ignore[attr-defined]
                                )
                                event = RunEventReq(
                                    kind=runner_proto.RunEventReqKind.START_WORKFLOW,
                                    execution=self.execution,
                                    start_workflow=start_payload,
                                )
                                child_final = yield event
                                assert (
                                    child_final.resp_type
                                    == RunEventResponseType.MESSAGE
                                )
                                assert child_final.message is not None
                                tool_responses.append(
                                    state.ToolCallResp(
                                        id=req.id,
                                        status=state.ToolCallStatus.COMPLETED,
                                        name=req.name,
                                        result={
                                            "agent_name": start_payload.workflow_name,
                                            "response": child_final.message.text,
                                        },
                                    )
                                )
                                continue

                        for req in approved:
                            tool_step = tool_request_steps.get(id(req))
                            if tool_step is None:
                                continue
                            req.status = state.ToolCallReqStatus.COMPLETE
                            req.handled_at = utcnow()
                            tool_step.is_final = True
                            status_event = RunEventReq(
                                kind=runner_proto.RunEventReqKind.STEP,
                                execution=self.execution,
                                step=tool_step,
                            )
                            _ = yield status_event

                    if tool_responses:
                        msg.tool_call_responses = list(tool_responses)

                        resp_by_id: dict[str, list[state.ToolCallResp]] = {}
                        for resp in tool_responses:
                            resp_list = resp_by_id.get(resp.id)
                            if resp_list is None:
                                resp_list = []
                                resp_by_id[resp.id] = resp_list
                            resp_list.append(resp)

                        for req in msg.tool_call_requests:
                            per_req = resp_by_id.get(req.id)
                            if per_req is None:
                                continue
                            tool_step = tool_request_steps.get(id(req))
                            if tool_step is None:
                                continue
                            tool_message = tool_step.message
                            if tool_message is None:
                                continue
                            tool_message.tool_call_responses = list(per_req)
                            tool_step.message = tool_message
                            persisted_tool_step = self._persist_step(tool_step)
                            tool_event = RunEventReq(
                                kind=runner_proto.RunEventReqKind.STEP,
                                execution=self.execution,
                                step=persisted_tool_step,
                            )
                            _ = yield tool_event

                        last_complete_step.message = msg
                        persisted_step = self._persist_step(last_complete_step)
                        event = RunEventReq(
                            kind=runner_proto.RunEventReqKind.STEP,
                            execution=self.execution,
                            step=persisted_step,
                        )
                        _ = yield event

                    continue

                confirmation_mode = current_runtime_node.model.confirmation
                loop_current_node = False

                if confirmation_mode in (
                    models.Confirmation.MANUAL,
                    models.Confirmation.LOOP,
                ):
                    while True:
                        prompt_message = state.Message(
                            role=models.Role.ASSISTANT,
                            text="",
                        )
                        prompt_type = state.StepType.PROMPT_CONFIRM
                        if confirmation_mode == models.Confirmation.LOOP:
                            prompt_type = state.StepType.PROMPT
                        prompt_step = state.Step(
                            execution=current_execution,
                            type=prompt_type,
                            message=prompt_message,
                        )
                        persisted_prompt = self._persist_step(prompt_step)

                        waiting_event = self.set_status(
                            state.RunnerStatus.WAITING_INPUT,
                            current_execution=current_execution,
                        )
                        _ = yield waiting_event

                        req_event = RunEventReq(
                            kind=runner_proto.RunEventReqKind.STEP,
                            execution=self.execution,
                            step=persisted_prompt,
                        )
                        resp_event = yield req_event
                        response_step = self._handle_run_event_response(
                            req_event, resp_event
                        )
                        response_event = self._build_response_event(response_step)
                        if response_event is not None:
                            _ = yield response_event

                        resume_status_event = self._set_running_after_input(
                            current_execution
                        )
                        if resume_status_event is not None:
                            _ = yield resume_status_event
                        if (
                            response_step is not None
                            and response_step.type == state.StepType.INPUT_MESSAGE
                        ):
                            if confirmation_mode == models.Confirmation.LOOP:
                                loop_current_node = True
                            else:
                                if (
                                    response_step.message is not None
                                    and response_step.message.text.strip()
                                ):
                                    loop_current_node = True
                            break

                        if (
                            response_step is not None
                            and response_step.type == state.StepType.APPROVAL
                        ):
                            if confirmation_mode == models.Confirmation.LOOP:
                                loop_current_node = True
                            break

                        continue

                if loop_current_node:
                    continue

                last_complete_step.is_final = True
                if last_complete_step.message is not None:
                    self._last_final_message = last_complete_step.message

                outcomes = current_runtime_node.model.outcomes

                outcome_name: Optional[str] = None
                if not outcomes:
                    self._set_node_execution_status(
                        current_execution, state.RunStatus.FINISHED
                    )
                    status_event = self.set_status(
                        state.RunnerStatus.FINISHED,
                        current_execution=current_execution,
                    )
                    _ = yield status_event
                    return

                next_runtime_node = None

                if len(outcomes) == 1:
                    children = current_runtime_node.children
                    if children:
                        next_runtime_node = children[0]
                        outcome_name = outcomes[0].name
                else:
                    outcome_name = last_complete_step.outcome_name
                    if not outcome_name:
                        req_event = self._create_transition_error_event(
                            current_execution,
                            "Missing outcome_name for completion step on node with multiple outcomes",
                        )
                        resp_event = yield req_event
                        response_step = self._handle_run_event_response(
                            req_event, resp_event
                        )
                        response_event = self._build_response_event(response_step)
                        if response_event is not None:
                            _ = yield response_event
                        current_execution.status = state.RunStatus.FINISHED
                        status_event = self.set_status(
                            state.RunnerStatus.FINISHED,
                            current_execution=current_execution,
                        )
                        _ = yield status_event
                        return

                    next_runtime_node = current_runtime_node.get_child_by_outcome(
                        outcome_name
                    )
                    if next_runtime_node is None:
                        req_event = self._create_transition_error_event(
                            current_execution,
                            f"Unknown outcome '{outcome_name}' for node '{current_runtime_node.name}'",
                        )
                        resp_event = yield req_event
                        response_step = self._handle_run_event_response(
                            req_event, resp_event
                        )
                        response_event = self._build_response_event(response_step)
                        if response_event is not None:
                            _ = yield response_event
                        current_execution.status = state.RunStatus.FINISHED
                        status_event = self.set_status(
                            state.RunnerStatus.FINISHED,
                            current_execution=current_execution,
                        )
                        _ = yield status_event
                        return

                if next_runtime_node is None:
                    self._set_node_execution_status(
                        current_execution, state.RunStatus.FINISHED
                    )
                    status_event = self.set_status(
                        state.RunnerStatus.FINISHED,
                        current_execution=current_execution,
                    )
                    _ = yield status_event
                    return

                edge_reset_policy = None
                if outcome_name is not None:
                    graph_model = self.graph.graph
                    for edge in graph_model.edges:
                        if (
                            edge.source_node == current_runtime_node.name
                            and edge.source_outcome == outcome_name
                            and edge.target_node == next_runtime_node.name
                        ):
                            edge_reset_policy = edge.reset_policy
                            break

                effective_reset_policy = next_runtime_node.model.reset_policy
                if edge_reset_policy is not None:
                    effective_reset_policy = edge_reset_policy

                next_input_messages = self._build_next_input_messages(
                    current_execution,
                    current_runtime_node.model,
                )

                self._set_node_execution_status(
                    current_execution, state.RunStatus.FINISHED
                )
                current_runtime_node = next_runtime_node

                previous_for_next: Optional[state.NodeExecution] = None
                if effective_reset_policy == models.StateResetPolicy.KEEP:
                    previous_for_next = self._find_node_execution(
                        current_runtime_node.name
                    )

                current_execution = self._create_node_execution(
                    current_runtime_node.name,
                    input_messages=next_input_messages,
                    previous_execution=previous_for_next,
                )

                status_event = self.set_status(
                    self.status,
                    current_execution=current_execution,
                )
                _ = yield status_event

        except RunnerStopped:
            if (
                current_execution is not None
                and current_execution.status == state.RunStatus.RUNNING
            ):
                self._set_node_execution_status(
                    current_execution, state.RunStatus.STOPPED
                )
            stop_event = self.set_status(
                state.RunnerStatus.STOPPED,
                current_execution=current_execution,
            )
            _ = yield stop_event
            return
        finally:
            await self._shutdown_executors()

    def set_status(
        self,
        status: state.RunnerStatus,
        current_execution: Optional[state.NodeExecution],
    ) -> RunEventReq:
        self.status = status
        current_node_name: Optional[str] = None
        current_node_execution_id = None
        if current_execution is not None:
            current_node_name = current_execution.node
            current_node_execution_id = current_execution.id
        stats = runner_proto.RunStats(
            status=status,
            current_node_name=current_node_name,
            current_node_execution_id=current_node_execution_id,
        )
        return RunEventReq(
            kind=runner_proto.RunEventReqKind.STATUS,
            execution=self.execution,
            stats=stats,
        )

    def _set_running_after_input(
        self,
        current_execution: Optional[state.NodeExecution],
    ) -> Optional[RunEventReq]:
        if self.status in (
            state.RunnerStatus.STOPPED,
            state.RunnerStatus.FINISHED,
        ):
            return None
        if self.status != state.RunnerStatus.WAITING_INPUT:
            return None
        return self.set_status(
            state.RunnerStatus.RUNNING,
            current_execution=current_execution,
        )

    def _apply_llm_usage(self, usage: Optional[state.LLMUsageStats]) -> None:
        if usage is None:
            return
        self.execution.last_step_llm_usage = state.LLMUsageStats(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_dollars=usage.cost_dollars,
            input_token_limit=usage.input_token_limit,
            output_token_limit=usage.output_token_limit,
        )
        execution_usage = self.execution.llm_usage
        if execution_usage is None:
            self.execution.llm_usage = state.LLMUsageStats(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_dollars=usage.cost_dollars,
                input_token_limit=usage.input_token_limit,
                output_token_limit=usage.output_token_limit,
            )
        else:
            execution_usage.prompt_tokens += usage.prompt_tokens
            execution_usage.completion_tokens += usage.completion_tokens
            execution_usage.cost_dollars += usage.cost_dollars
            if (
                execution_usage.input_token_limit is None
                and usage.input_token_limit is not None
            ):
                execution_usage.input_token_limit = usage.input_token_limit
            if (
                execution_usage.output_token_limit is None
                and usage.output_token_limit is not None
            ):
                execution_usage.output_token_limit = usage.output_token_limit
        self.project.add_llm_usage(
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cost_dollars,
        )
        self._touch_execution()
