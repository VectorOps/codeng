from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from pydantic import Field

from vocode import models, state
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput


class NoopNode(models.Node):
    type: str = "noop"

    confirmation: models.Confirmation = Field(
        default=models.Confirmation.AUTO,
        description="No-op auto confirmation.",
    )

    message_mode: models.ResultMode = Field(
        default=models.ResultMode.ALL_MESSAGES,
        description="No-op defaults to passing all messages to the next node.",
    )

    sleep_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="If set, sleep for this many seconds before producing the final response.",
    )
 
@ExecutorFactory.register("noop")
class NoopExecutor(BaseExecutor):
    def __init__(self, config: NoopNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config

        delay = cfg.sleep_seconds
        if delay is not None and delay > 0:
            await asyncio.sleep(delay)

        outcome_name: Optional[str] = None
        outcomes = cfg.outcomes or []

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
            text="",
        )
        step = state.Step(
            execution=inp.execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=message,
            is_complete=True,
            is_final=True,
            outcome_name=outcome_name,
        )
        yield step