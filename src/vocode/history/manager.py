from __future__ import annotations

from typing import Optional
from uuid import UUID

from vocode import models, state
from . import models as history_models


class HistoryManager:
    def get_visible_step_ids(self, execution: state.WorkflowExecution) -> list[UUID]:
        return list(execution.step_ids)

    def get_last_user_input_step(
        self,
        execution: state.WorkflowExecution,
    ) -> Optional[state.Step]:
        for step in execution.iter_steps_reversed():
            message = step.message
            if (
                step.type == state.StepType.INPUT_MESSAGE
                and message is not None
                and message.role == models.Role.USER
            ):
                return step
        return None

    def compute_view_diff(
        self,
        before_visible_ids: list[UUID],
        after_visible_ids: list[UUID],
    ) -> list[UUID]:
        after_ids = set(after_visible_ids)
        return [step_id for step_id in before_visible_ids if step_id not in after_ids]

    def switch_branch(
        self,
        execution: state.WorkflowExecution,
        branch_id: UUID,
    ) -> history_models.HistoryMutationResult:
        before_visible_ids = execution.get_active_step_ids()
        branch = execution.switch_branch(branch_id)
        after_visible_ids = execution.get_active_step_ids()
        removed_visible_step_ids = self.compute_view_diff(
            before_visible_ids,
            after_visible_ids,
        )
        added_visible_ids = [
            step_id
            for step_id in after_visible_ids
            if step_id not in set(before_visible_ids)
        ]
        return history_models.HistoryMutationResult(
            changed=before_visible_ids != after_visible_ids,
            active_branch_id=branch.id,
            removed_visible_step_ids=removed_visible_step_ids,
            upserted_steps=[
                execution.get_step(step_id) for step_id in added_visible_ids
            ],
            resume_step_id=branch.head_step_id,
        )

    def edit_user_input(
        self,
        execution: state.WorkflowExecution,
        step_id: UUID,
        text: str,
    ) -> history_models.HistoryMutationResult:
        target_step = execution.get_step(step_id)
        message = target_step.message
        if message is None or message.role != models.Role.USER:
            return history_models.HistoryMutationResult(changed=False)
        before_visible_ids = execution.get_active_step_ids()
        created_branch = execution.create_branch(
            head_step_id=target_step.parent_step_id,
            base_step_id=target_step.id,
            activate=True,
        )
        replacement_message = state.Message(
            role=message.role,
            text=text,
            thinking_content=message.thinking_content,
        )
        execution.add_message(replacement_message)
        replacement_step = execution.create_step(
            execution_id=target_step.execution_id,
            parent_step_id=target_step.parent_step_id,
            type=target_step.type,
            message_id=replacement_message.id,
            content_type=target_step.content_type,
            output_mode=target_step.output_mode,
            outcome_name=target_step.outcome_name,
            state=target_step.state,
            status_hint=target_step.status_hint,
            llm_usage=target_step.llm_usage,
            is_complete=target_step.is_complete,
            is_final=False,
        )
        after_visible_ids = execution.get_active_step_ids()
        removed_visible_step_ids = self.compute_view_diff(
            before_visible_ids,
            after_visible_ids,
        )
        added_visible_ids = [
            visible_step_id
            for visible_step_id in after_visible_ids
            if visible_step_id not in set(before_visible_ids)
        ]
        return history_models.HistoryMutationResult(
            changed=True,
            active_branch_id=execution.active_branch_id,
            created_branch_id=created_branch.id,
            resume_step_id=replacement_step.id,
            removed_visible_step_ids=removed_visible_step_ids,
            upserted_steps=[
                execution.get_step(step_id) for step_id in added_visible_ids
            ],
        )
