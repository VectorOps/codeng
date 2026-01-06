from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional, TYPE_CHECKING

from pydantic import Field, model_validator

from vocode import models, state
from vocode.patch import apply_patch, get_supported_formats
from vocode.runner.base import BaseExecutor, ExecutorInput

if TYPE_CHECKING:
    from vocode.project import Project


class ApplyPatchNode(models.Node):
    type: str = "apply_patch"

    format: str = Field(
        default="v4a",
        description="Patch format identifier ('v4a' or 'patch')",
    )

    @model_validator(mode="after")
    def _validate_reset_policy(self) -> "ApplyPatchNode":
        if self.reset_policy != models.StateResetPolicy.RESET:
            raise ValueError(
                "ApplyPatchNode: reset_policy must be 'reset' for apply_patch nodes"
            )
        return self


class ApplyPatchExecutor(BaseExecutor):
    type = "apply_patch"

    def __init__(self, config: ApplyPatchNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config
        execution = inp.execution

        fmt = (cfg.format or "v4a").lower()
        supported = set(get_supported_formats())

        if fmt not in supported:
            supported_list = ", ".join(sorted(supported))
            message = state.Message(
                role=models.Role.ASSISTANT,
                text=(
                    f"Unsupported patch format: {fmt}. "
                    f"Supported formats: {supported_list}"
                ),
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=message,
                is_complete=True,
                is_final=True,
                outcome_name="fail",
            )
            yield step
            return

        source_text: Optional[str] = None
        if execution.input_messages:
            last_msg = execution.input_messages[-1]
            source_text = last_msg.text or ""

        if not source_text or not source_text.strip():
            message = state.Message(
                role=models.Role.ASSISTANT,
                text="No patch was provided. The patch application has failed.",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=message,
                is_complete=True,
                is_final=True,
                outcome_name="fail",
            )
            yield step
            return

        try:
            base_path = self.project.base_path  # type: ignore[attr-defined]
        except Exception:
            message = state.Message(
                role=models.Role.SYSTEM,
                text="ApplyPatchExecutor requires project.base_path",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=message,
                is_complete=True,
                is_final=True,
                outcome_name="fail",
            )
            yield step
            return

        try:
            from vocode.project import FileChangeModel, FileChangeType

            summary, outcome_name, changes_map, _statuses, _errs = apply_patch(
                fmt,
                source_text,
                base_path,
            )

            change_type_map = {
                "created": FileChangeType.CREATED,
                "updated": FileChangeType.UPDATED,
                "deleted": FileChangeType.DELETED,
            }
            changed_files = [
                FileChangeModel(type=change_type_map[kind], relative_filename=rel)
                for rel, kind in changes_map.items()
                if kind in change_type_map
            ]
            if changed_files:
                asyncio.create_task(
                    self.project.refresh(files=changed_files)  # type: ignore[attr-defined]
                )

            message = state.Message(
                role=models.Role.ASSISTANT,
                text=summary,
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
        except Exception as e:
            message = state.Message(
                role=models.Role.ASSISTANT,
                text=f"Error applying patch: {e}",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=message,
                is_complete=True,
                is_final=True,
                outcome_name="fail",
            )
            yield step
