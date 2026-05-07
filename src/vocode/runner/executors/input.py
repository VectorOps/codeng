from typing import AsyncIterator, Optional

from pydantic import Field

from vocode import models, state
from vocode.runner import base as runner_base


class InputNode(models.Node):
    type: str = "input"

    message: Optional[str] = Field(
        default=None,
        description="Optional prompt message shown to the user when requesting input.",
    )
    accepted_input_type: Optional[str] = Field(
        default=None,
        description=(
            "Optional input type accepted by this node. Defaults to interactive input."
        ),
    )
    confirmation: models.Confirmation = Field(
        default=models.Confirmation.AUTO,
        description="Input nodes default to automatic confirmation after receiving user input.",
    )


@runner_base.ExecutorFactory.register("input")
class InputExecutor(runner_base.BaseExecutor):

    def __init__(self, config: InputNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: runner_base.ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        history = self.project.history

        input_message: Optional[state.Message] = None
        for msg, step in runner_base.iter_execution_messages(execution):
            if step is not None and step.type == state.StepType.INPUT_MESSAGE:
                input_message = msg

        if input_message is None:
            prompt_text = self.config.message or ""
            prompt_message = state.Message(
                role=models.Role.ASSISTANT,
                text=prompt_text,
            )
            history.upsert_message(inp.run, prompt_message)
            prompt_step = history.upsert_step(
                inp.run,
                state.Step(
                    workflow_execution=inp.run,
                    execution_id=execution.id,
                    type=state.StepType.PROMPT,
                    message_id=prompt_message.id,
                    is_complete=True,
                ),
            )
            yield prompt_step
            return

        output_step = history.upsert_step(
            inp.run,
            state.Step(
                workflow_execution=inp.run,
                execution_id=execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
                message_id=input_message.id,
                is_complete=True,
                is_final=True,
            ),
        )
        yield output_step
