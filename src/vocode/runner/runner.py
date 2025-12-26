from typing import Optional, Dict, AsyncIterator

from vocode import models, state
from vocode import settings as vocode_settings
from vocode.project import Project
from vocode.graph import RuntimeGraph
from vocode.lib import validators
from .base import BaseExecutor, ExecutorInput
from pydantic import BaseModel, Field


class RunEvent(BaseModel):
    run: state.WorkflowExecution = Field(...)
    step: state.Step = Field(...)


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

    def _ensure_node_execution(self, node_name: str) -> state.NodeExecution:
        for execution in self.execution.node_executions.values():
            if execution.node == node_name:
                return execution

        execution = state.NodeExecution(
            node=node_name,
            status=state.RunStatus.RUNNING,
        )
        if self.initial_message is not None and not self.execution.steps:
            execution.input_messages.append(self.initial_message)
        self.execution.node_executions[execution.id] = execution
        return execution

    # Main runner loop
    async def run(self) -> AsyncIterator[RunEvent]:
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

        if current_runtime_node is None:
            self.status = state.RunnerStatus.FINISHED
            return

        while True:
            if (
                current_execution is None
                or current_execution.node != current_runtime_node.name
            ):
                current_execution = self._ensure_node_execution(
                    current_runtime_node.name
                )

            executor = self._executors[current_runtime_node.name]
            executor_input = ExecutorInput(
                execution=current_execution, run=self.execution
            )

            completion_step: Optional[state.Step] = None
            tool_message_step: Optional[state.Step] = None

            async for step in executor.run(executor_input):
                persisted_step = self._persist_step(step)
                response = yield RunEvent(run=self.execution, step=persisted_step)
                self._persist_step(response)

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
                            prompt_message = state.Message(
                                role=models.Role.ASSISTANT,
                                text="",
                                tool_call_requests=[req],
                            )
                            prompt_step = state.Step(
                                execution=current_execution,
                                type=state.StepType.PROMPT,
                                message=prompt_message,
                            )
                            persisted_prompt = self._persist_step(prompt_step)
                            response = yield RunEvent(
                                run=self.execution,
                                step=persisted_prompt,
                            )
                            self._persist_step(response)

                            if response.type == state.StepType.APPROVAL:
                                approved.append(req)
                                break

                            if response.type == state.StepType.REJECTION:
                                parts = ["A user rejected the tool call."]
                                if response.message is not None:
                                    text = response.message.text.strip()
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
                                break

                    for req in approved:
                        resp = await self._execute_tool_call(req)
                        tool_responses.append(resp)

                    if tool_responses:
                        tool_message = state.Message(
                            role=models.Role.ASSISTANT,
                            text="",
                            tool_call_responses=tool_responses,
                        )
                        tool_step = state.Step(
                            execution=current_execution,
                            type=state.StepType.INPUT_MESSAGE,
                            message=tool_message,
                            is_complete=True,
                        )
                        self._persist_step(tool_step)

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
                    response = yield RunEvent(run=self.execution, step=persisted_prompt)
                    self._persist_step(response)

                    if response.type == state.StepType.INPUT_MESSAGE:
                        if (
                            response.message is not None
                            and response.message.text.strip()
                        ):
                            loop_current_node = True
                        break

                    if response.type == state.StepType.APPROVAL:
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
                    response = yield RunEvent(run=self.execution, step=persisted_error)
                    self._persist_step(response)
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
                    response = yield RunEvent(run=self.execution, step=persisted_error)
                    self._persist_step(response)
                    current_execution.status = state.RunStatus.FINISHED
                    self.status = state.RunnerStatus.FINISHED
                    return

            if next_runtime_node is None:
                current_execution.status = state.RunStatus.FINISHED
                self.status = state.RunnerStatus.FINISHED
                return

            current_execution.status = state.RunStatus.FINISHED
            current_runtime_node = next_runtime_node
            current_execution = None
