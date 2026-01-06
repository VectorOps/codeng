from __future__ import annotations

from typing import AsyncIterator, Optional

from pydantic import Field

from vocode import models, state
from vocode.runner.base import BaseExecutor, ExecutorInput


class ResultNode(models.Node):
    type: str = "result"

    confirmation: models.Confirmation = Field(
        default=models.Confirmation.AUTO,
        description="Result node auto confirmation.",
    )


class ResultExecutor(BaseExecutor):
    type = "result"

    def __init__(self, config: ResultNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution

        texts = [m.text for m in execution.input_messages if m.text]
        combined = "\n".join(texts)

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
            text=combined,
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=message,
            is_complete=True,
            is_final=True,
            outcome_name=outcome_name,
        )
        yield step