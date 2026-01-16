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
            prompt_step = state.Step(
                execution=execution,
                type=state.StepType.PROMPT,
                message=prompt_message,
                is_complete=True,
            )
            yield prompt_step
            return

        output_step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=input_message,
            is_complete=True,
            is_final=True,
        )
        yield output_step
