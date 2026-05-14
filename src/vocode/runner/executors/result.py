from typing import AsyncIterator, Optional

from pydantic import Field

from vocode import models, state
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput


class ResultNode(models.Node):
    type: str = "result"
    message: Optional[str] = Field(
        default=None,
        description="Optional assistant message emitted by this result node.",
    )
    confirmation: models.Confirmation = Field(
        default=models.Confirmation.AUTO,
        description="Result node auto confirmation.",
    )


@ExecutorFactory.register("result")
class ResultExecutor(BaseExecutor):
    def __init__(self, config: ResultNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        history = self.project.history

        text = self.config.message
        if text is None:
            texts = [m.text for m in execution.input_messages if m.text]
            text = "\n".join(texts)

        outcome_name: Optional[str] = None
        outcomes = self.config.outcomes or []
        if len(outcomes) == 1:
            outcome_name = outcomes[0].name
        elif len(outcomes) > 1:
            names = [o.name for o in outcomes]
            for pref in ("next", "success"):
                if pref in names:
                    outcome_name = pref
                    break
            if outcome_name is None:
                outcome_name = outcomes[0].name

        message = state.Message(
            role=models.Role.ASSISTANT,
            text=text,
        )
        history.upsert_message(inp.run, message)
        step = history.upsert_step(
            inp.run,
            state.Step(
                workflow_execution=inp.run,
                execution_id=execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
                message_id=message.id,
                is_complete=True,
                is_final=True,
                outcome_name=outcome_name,
            ),
        )
        yield step
