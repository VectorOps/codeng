from typing import AsyncIterator, Optional

from pydantic import Field

from vocode import state
from vocode.models import Node, Role
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput


class RunAgentNode(Node):
    """Node that starts a nested workflow/agent in a new runner frame.

    This replaces the former StartWorkflowNode. The underlying runner packet
    remains ReqStartWorkflow(kind="start_workflow") for wire compatibility.
    """

    type: str = Field(default="run_agent")
    workflow: str = Field(
        ..., description="Name of the workflow/agent to start in a new runner frame"
    )
    initial_text: Optional[str] = Field(
        default=None,
        description="Optional initial user message text for the child agent",
    )


@ExecutorFactory.register("run_agent")
class RunAgentExecutor(BaseExecutor):
    config: RunAgentNode

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        # Check if we already have a result from the child workflow
        result_step = next(
            (
                s
                for s in inp.execution.steps
                if s.type == state.StepType.WORKFLOW_RESULT
            ),
            None,
        )
        if result_step:
            # We have a result. Emit it as final output.
            yield state.Step(
                execution=inp.execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=result_step.message,
                is_complete=True,
                is_final=True,
            )
            return

        # Check if we already requested the workflow
        req_step = next(
            (
                s
                for s in inp.execution.steps
                if s.type == state.StepType.WORKFLOW_REQUEST
            ),
            None,
        )
        if req_step:
            # Request sent but no result yet. Return to let Runner handle it.
            # We yield the request step again to ensure runner sees a complete step.
            yield req_step
            return

        # Create new request
        msg = state.Message(
            role=Role.ASSISTANT, text=self.config.initial_text or ""
        )
        req_step = state.Step(
            execution=inp.execution,
            type=state.StepType.WORKFLOW_REQUEST,
            message=msg,
            is_complete=True,
        )
        yield req_step