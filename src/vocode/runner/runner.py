from typing import Optional, Dict, AsyncIterator

from vocode import models, state
from vocode import settings as vocode_settings
from vocode.project import Project
from vocode.graph import RuntimeGraph
from vocode.lib import validators
from .base import BaseExecutor, ExecutorInput
from .proto import RunEventReq, RunEventResp, RunEventResponseType


RunEvent = RunEventReq


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

        self.status = state.RunnerStatus.IDLE
        self.graph = RuntimeGraph(workflow.graph)
        self.execution = state.WorkflowExecution(workflow_name=workflow.name)

        self._executors: Dict[str, BaseExecutor] = {
            n.name: BaseExecutor.create_for_node(n, project=self.project)
            for n in self.workflow.graph.nodes
        }

    # Tool calling
    def _get_tool_spec_for_request(
        self, req: state.ToolCallReq
    ) -> Optional[vocode_settings.ToolSpec]:
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

    async def _execute_tool_call(self, req: state.ToolCallReq) -> state.ToolCallResp:
        spec = self._get_tool_spec_for_request(req)
        tools = self.project.tools
        tool = tools.get(req.name)
        if tool is None:
            return state.ToolCallResp(
                id=req.id,
                status=state.ToolCallStatus.FAILED,
                name=req.name,
                result={"error": f"Tool '{req.name}' is not available."},
            )
        if spec is None:
            spec = vocode_settings.ToolSpec(name=req.name)
        try:
            raw_result = await tool.run(spec, req.arguments)
        except Exception as exc:
            return state.ToolCallResp(
                id=req.id,
                status=state.ToolCallStatus.FAILED,
                name=req.name,
                result={"error": str(exc)},
            )
        if raw_result is None:
            result_data = None
        else:
            result_data = raw_result.model_dump()
        return state.ToolCallResp(
            id=req.id,
            status=state.ToolCallStatus.COMPLETED,
            name=req.name,
            result=result_data,
        )

    def _create_tool_prompt_step(
        self,
        execution: state.NodeExecution,
        req: state.ToolCallReq,
    ) -> state.Step:
        prompt_message = state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        )
        prompt_step = state.Step(
            execution=execution,
            type=state.StepType.PROMPT,
            message=prompt_message,
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
            return True
        return False

    async def _execute_approved_tool_calls(
        self,
        approved: list[state.ToolCallReq],
    ) -> list[state.ToolCallResp]:
        responses: list[state.ToolCallResp] = []
        for req in approved:
            resp = await self._execute_tool_call(req)
            responses.append(resp)
        return responses

    def _create_tool_result_step(
        self,
        execution: state.NodeExecution,
        tool_responses: list[state.ToolCallResp],
    ) -> state.Step:
        tool_message = state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_responses=tool_responses,
        )
        tool_step = state.Step(
            execution=execution,
            type=state.StepType.INPUT_MESSAGE,
            message=tool_message,
            is_complete=True,
        )
        return self._persist_step(tool_step)

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
            return [final_message] if final_message is not None else []

        if mode == models.ResultMode.CONCATENATE_FINAL:
            if not execution.input_messages and final_message is None:
                return []

            text_parts: list[str] = []
            for m in execution.input_messages:
                if m.text:
                    text_parts.append(m.text)
            if final_message is not None and final_message.text:
                text_parts.append(final_message.text)

            combined_text = "\n\n".join(text_parts)

            if final_message is not None:
                role = final_message.role
            elif execution.input_messages:
                role = execution.input_messages[-1].role
            else:
                role = models.Role.USER

            tool_call_requests: list[state.ToolCallReq] = []
            tool_call_responses: list[state.ToolCallResp] = []
            if final_message is not None:
                tool_call_requests = list(final_message.tool_call_requests)
                tool_call_responses = list(final_message.tool_call_responses)

            combined_message = state.Message(
                role=role,
                text=combined_text,
                tool_call_requests=tool_call_requests,
                tool_call_responses=tool_call_responses,
            )
            return [combined_message]

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
        return execution

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
        return self._persist_step(step)

    # Main runner loop
    async def run(self) -> AsyncIterator[RunEventReq]:
        if self.status not in (state.RunnerStatus.IDLE, state.RunnerStatus.STOPPED):
            raise RuntimeError(
                f"run() not allowed when runner status is '{self.status}'. Allowed: 'idle', 'stopped'"
            )

        self.status = state.RunnerStatus.RUNNING

        current_runtime_node = None
        current_execution: Optional[state.NodeExecution] = None

        if self.execution.steps:
            last_step = self.execution.steps[-1]
            current_execution = last_step.execution
            current_runtime_node = self.graph.get_runtime_node_by_name(
                current_execution.node
            )
        else:
            current_runtime_node = self.graph.root
            if current_runtime_node is not None:
                existing_execution = self._find_node_execution(
                    current_runtime_node.name
                )
                if existing_execution is not None:
                    current_execution = existing_execution
                else:
                    initial_messages: list[state.Message] = []
                    if self.initial_message is not None:
                        initial_messages.append(self.initial_message)
                    current_execution = self._create_node_execution(
                        current_runtime_node.name,
                        input_messages=initial_messages,
                    )

        if current_runtime_node is None:
            self.status = state.RunnerStatus.FINISHED
            return

        while True:
            # current_execution should already refer to the execution for the current node

            executor = self._executors[current_runtime_node.name]
            executor_input = ExecutorInput(
                execution=current_execution, run=self.execution
            )

            completion_step: Optional[state.Step] = None
            tool_message_step: Optional[state.Step] = None

            async for step in executor.run(executor_input):
                persisted_step = self._persist_step(step)
                req = RunEventReq(execution=self.execution, step=persisted_step)
                resp = yield req
                self._handle_run_event_response(req, resp)

                if (
                    persisted_step.message is not None
                    and persisted_step.is_complete
                    and persisted_step.message.tool_call_requests
                ):
                    tool_message_step = persisted_step

                if step.type == state.StepType.COMPLETION:
                    completion_step = step
                    break

            if completion_step is None:
                current_execution.status = state.RunStatus.FINISHED
                self.status = state.RunnerStatus.FINISHED
                return

            if tool_message_step is not None:
                msg = tool_message_step.message
                if msg is not None and msg.tool_call_requests:
                    approved: list[state.ToolCallReq] = []
                    tool_responses: list[state.ToolCallResp] = []

                    for req in msg.tool_call_requests:
                        if self._is_tool_call_auto_approved(req):
                            approved.append(req)
                            continue

                        while True:
                            persisted_prompt = self._create_tool_prompt_step(
                                current_execution,
                                req,
                            )
                            req_event = RunEventReq(
                                execution=self.execution,
                                step=persisted_prompt,
                            )
                            resp_event = yield req_event
                            response_step = self._handle_run_event_response(
                                req_event, resp_event
                            )

                            if self._process_tool_approval_response(
                                req,
                                response_step,
                                approved,
                                tool_responses,
                            ):
                                break

                    if approved:
                        responses = await self._execute_approved_tool_calls(approved)
                        tool_responses.extend(responses)

                    if tool_responses:
                        self._create_tool_result_step(
                            current_execution,
                            tool_responses,
                        )

                continue

            confirmation_mode = current_runtime_node.model.confirmation
            loop_current_node = False

            if confirmation_mode == models.Confirmation.MANUAL:
                while True:
                    prompt_message = state.Message(
                        role=models.Role.ASSISTANT,
                        text="",
                    )
                    prompt_step = state.Step(
                        execution=current_execution,
                        type=state.StepType.PROMPT,
                        message=prompt_message,
                    )
                    persisted_prompt = self._persist_step(prompt_step)
                    req_event = RunEventReq(
                        execution=self.execution,
                        step=persisted_prompt,
                    )
                    resp_event = yield req_event
                    response_step = self._handle_run_event_response(
                        req_event, resp_event
                    )

                    if (
                        response_step is not None
                        and response_step.type == state.StepType.INPUT_MESSAGE
                    ):
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
                        break

                    continue

            if loop_current_node:
                continue

            outcomes = current_runtime_node.model.outcomes

            if not outcomes:
                current_execution.status = state.RunStatus.FINISHED
                self.status = state.RunnerStatus.FINISHED
                return

            next_runtime_node = None

            if len(outcomes) == 1:
                children = current_runtime_node.children
                if children:
                    next_runtime_node = children[0]
            else:
                outcome_name = completion_step.outcome_name if completion_step else None
                if not outcome_name:
                    error_message = state.Message(
                        role=models.Role.SYSTEM,
                        text="Missing outcome_name for completion step on node with multiple outcomes",
                    )
                    error_step = state.Step(
                        execution=current_execution,
                        type=state.StepType.REJECTION,
                        message=error_message,
                    )
                    persisted_error = self._persist_step(error_step)
                    req_event = RunEventReq(
                        execution=self.execution,
                        step=persisted_error,
                    )
                    resp_event = yield req_event
                    self._handle_run_event_response(req_event, resp_event)
                    current_execution.status = state.RunStatus.FINISHED
                    self.status = state.RunnerStatus.FINISHED
                    return

                next_runtime_node = current_runtime_node.get_child_by_outcome(
                    outcome_name
                )
                if next_runtime_node is None:
                    error_message = state.Message(
                        role=models.Role.SYSTEM,
                        text=f"Unknown outcome '{outcome_name}' for node '{current_runtime_node.name}'",
                    )
                    error_step = state.Step(
                        execution=current_execution,
                        type=state.StepType.REJECTION,
                        message=error_message,
                    )
                    persisted_error = self._persist_step(error_step)
                    req_event = RunEventReq(
                        execution=self.execution,
                        step=persisted_error,
                    )
                    resp_event = yield req_event
                    self._handle_run_event_response(req_event, resp_event)
                    current_execution.status = state.RunStatus.FINISHED
                    self.status = state.RunnerStatus.FINISHED
                    return

            if next_runtime_node is None:
                current_execution.status = state.RunStatus.FINISHED
                self.status = state.RunnerStatus.FINISHED
                return
            next_input_messages = self._build_next_input_messages(
                current_execution,
                current_runtime_node.model,
            )

            current_execution.status = state.RunStatus.FINISHED
            current_runtime_node = next_runtime_node

            previous_for_next: Optional[state.NodeExecution] = None
            if current_runtime_node.model.reset_policy == models.StateResetPolicy.KEEP:
                previous_for_next = self._find_node_execution(current_runtime_node.name)

            current_execution = self._create_node_execution(
                current_runtime_node.name,
                input_messages=next_input_messages,
                previous_execution=previous_for_next,
            )
