from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel

from vocode import models, state
from . import models as history_models


class HistoryManager:
    def add_message(
        self,
        execution: state.WorkflowExecution,
        message: state.Message,
    ) -> state.Message:
        execution.messages_by_id[message.id] = message
        return message

    def get_visible_step_ids(self, execution: state.WorkflowExecution) -> list[UUID]:
        return list(execution.step_ids)

    def create_branch(
        self,
        execution: state.WorkflowExecution,
        *,
        head_step_id: Optional[UUID] = None,
        base_step_id: Optional[UUID] = None,
        label: Optional[str] = None,
        activate: bool = True,
    ) -> state.BranchRecord:
        branch = state.BranchRecord(
            head_step_id=head_step_id,
            base_step_id=base_step_id,
            label=label,
        )
        execution.branches_by_id[branch.id] = branch
        if activate:
            execution.active_branch_id = branch.id
            execution.refresh_visible_step_ids()
        return branch

    def create_node_execution(
        self,
        execution: state.WorkflowExecution,
        *,
        node: str,
        status: state.RunStatus,
        branch_id: Optional[UUID] = None,
        input_messages: Optional[list[state.Message]] = None,
        input_message_ids: Optional[list[UUID]] = None,
        previous_execution: Optional[state.NodeExecution] = None,
        previous_id: Optional[UUID] = None,
        runtime_state: Optional[BaseModel] = None,
    ) -> state.NodeExecution:
        resolved_input_ids = list(input_message_ids or [])
        if input_messages is not None:
            for message in input_messages:
                self.add_message(execution, message)
            resolved_input_ids = [message.id for message in input_messages]
        resolved_branch_id = branch_id
        if resolved_branch_id is None:
            resolved_branch_id = execution.get_active_branch().id
        resolved_previous_id = previous_id
        if previous_execution is not None:
            resolved_previous_id = previous_execution.id
        node_execution = state.NodeExecution(
            workflow_execution=execution,
            node=node,
            previous_id=resolved_previous_id,
            branch_id=resolved_branch_id,
            input_message_ids=resolved_input_ids,
            status=status,
            state=runtime_state,
        )
        execution.node_executions[node_execution.id] = node_execution
        return node_execution

    def create_step(
        self,
        execution: state.WorkflowExecution,
        *,
        id: Optional[UUID] = None,
        execution_id: UUID,
        type: state.StepType,
        message: Optional[state.Message] = None,
        message_id: Optional[UUID] = None,
        parent_step_id: Optional[UUID] = None,
        content_type: state.StepContentType = state.StepContentType.MARKDOWN,
        output_mode: models.OutputMode = models.OutputMode.SHOW,
        outcome_name: Optional[str] = None,
        runtime_state: Optional[BaseModel] = None,
        status_hint: Optional[state.RunnerStatus] = None,
        llm_usage: Optional[state.LLMUsageStats] = None,
        is_complete: bool = True,
        is_final: bool = False,
    ) -> state.Step:
        resolved_message_id = message_id
        if message is not None:
            self.add_message(execution, message)
            resolved_message_id = message.id
        step = state.Step(
            workflow_execution=execution,
            id=(id or uuid4()),
            execution_id=execution_id,
            parent_step_id=parent_step_id,
            type=type,
            message_id=resolved_message_id,
            content_type=content_type,
            output_mode=output_mode,
            outcome_name=outcome_name,
            state=runtime_state,
            status_hint=status_hint,
            llm_usage=llm_usage,
            is_complete=is_complete,
            is_final=is_final,
        )
        return self.append_step(execution, step)

    def append_step(
        self,
        execution: state.WorkflowExecution,
        step: state.Step,
    ) -> state.Step:
        node_execution = execution.get_node_execution(step.execution_id)
        if node_execution.branch_id is None:
            node_execution.branch_id = execution._ensure_default_branch().id
        branch = execution.get_branch(node_execution.branch_id)
        if (
            step.message_id is not None
            and step.message_id not in execution.messages_by_id
        ):
            raise KeyError(f"Unknown message id: {step.message_id}")
        if step.parent_step_id is None:
            step.parent_step_id = branch.head_step_id
        if step.parent_step_id is not None:
            parent_step = execution.get_step(step.parent_step_id)
            if step.id not in parent_step.child_step_ids:
                parent_step.child_step_ids.append(step.id)
        step._workflow_execution = execution
        execution.steps_by_id[step.id] = step
        if step.id not in node_execution.step_ids:
            node_execution.step_ids.append(step.id)
        branch.head_step_id = step.id
        if branch.base_step_id is None:
            path = execution._get_path_from_head(step.id)
            branch.base_step_id = path[0] if path else step.id
        if execution.active_branch_id == branch.id:
            execution.refresh_visible_step_ids()
        return step

    def upsert_step(
        self,
        execution: state.WorkflowExecution,
        step: state.Step,
    ) -> state.Step:
        if step.id not in execution.steps_by_id:
            return self.append_step(execution, step)
        node_execution = execution.get_node_execution(step.execution_id)
        if step.id not in node_execution.step_ids:
            node_execution.step_ids.append(step.id)
        step._workflow_execution = execution
        execution.steps_by_id[step.id] = step
        if execution.active_branch_id == node_execution.branch_id:
            execution.refresh_visible_step_ids()
        return step

    def delete_steps(
        self,
        execution: state.WorkflowExecution,
        step_ids: list[UUID],
    ) -> None:
        if not step_ids:
            return
        step_ids_set = set(step_ids)
        removed_parent_ids: dict[UUID, Optional[UUID]] = {}
        for step in execution.steps_by_id.values():
            step.child_step_ids = [
                child_step_id
                for child_step_id in step.child_step_ids
                if child_step_id not in step_ids_set
            ]
        for step in list(execution.steps_by_id.values()):
            if step.id in step_ids_set:
                removed_parent_ids[step.id] = step.parent_step_id
                execution.steps_by_id.pop(step.id, None)
                continue
            if step.parent_step_id in step_ids_set:
                step.parent_step_id = None
        for node_execution in execution.node_executions.values():
            node_execution.step_ids = [
                step_id
                for step_id in node_execution.step_ids
                if step_id not in step_ids_set
            ]
        for branch in execution.branches_by_id.values():
            if branch.head_step_id in step_ids_set:
                branch.head_step_id = execution._find_visible_ancestor(
                    branch.head_step_id,
                    removed_parent_ids,
                )
            if branch.base_step_id in step_ids_set:
                branch.base_step_id = execution._find_visible_ancestor(
                    branch.base_step_id,
                    removed_parent_ids,
                )
        execution._ensure_default_branch()
        execution.refresh_visible_step_ids()

    def delete_step(
        self,
        execution: state.WorkflowExecution,
        step_id: UUID,
    ) -> None:
        self.delete_steps(execution, [step_id])

    def delete_node_execution(
        self,
        execution: state.WorkflowExecution,
        execution_id: UUID,
    ) -> None:
        node_execution = execution.node_executions.get(execution_id)
        if node_execution is None:
            return
        if node_execution.step_ids:
            self.delete_steps(execution, list(node_execution.step_ids))
        execution.node_executions.pop(execution_id, None)

    def trim_empty_node_executions(
        self,
        execution: state.WorkflowExecution,
    ) -> None:
        empty_execution_ids = [
            node_execution.id
            for node_execution in execution.node_executions.values()
            if not node_execution.step_ids
        ]
        for execution_id in empty_execution_ids:
            self.delete_node_execution(execution, execution_id)

    def find_node_execution(
        self,
        execution: state.WorkflowExecution,
        node_name: str,
    ) -> Optional[state.NodeExecution]:
        seen_execution_ids: set[UUID] = set()
        for step in execution.iter_steps_reversed():
            step_execution = step.execution
            if step_execution.id in seen_execution_ids:
                continue
            seen_execution_ids.add(step_execution.id)
            if step_execution.node == node_name:
                return step_execution
        latest: Optional[state.NodeExecution] = None
        active_branch_id = execution.active_branch_id
        for node_execution in execution.node_executions.values():
            if node_execution.node != node_name:
                continue
            if (
                active_branch_id is not None
                and node_execution.branch_id != active_branch_id
            ):
                continue
            if latest is None or node_execution.created_at > latest.created_at:
                latest = node_execution
        return latest

    def _fork_node_execution(
        self,
        execution: state.WorkflowExecution,
        source_execution: state.NodeExecution,
        branch_id: UUID,
    ) -> state.NodeExecution:
        return self.create_node_execution(
            execution,
            node=source_execution.node,
            status=source_execution.status,
            branch_id=branch_id,
            input_message_ids=list(source_execution.input_message_ids),
            previous_id=source_execution.previous_id,
            runtime_state=source_execution.state,
        )

    def fork_from_step(
        self,
        execution: state.WorkflowExecution,
        from_step_id: Optional[UUID],
        new_step: state.Step,
        *,
        base_step_id: Optional[UUID] = None,
        label: Optional[str] = None,
        activate: bool = True,
    ) -> history_models.HistoryMutationResult:
        before_visible_ids = self.get_visible_step_ids(execution)
        created_branch = self.create_branch(
            execution,
            head_step_id=from_step_id,
            base_step_id=base_step_id,
            label=label,
            activate=activate,
        )
        target_execution = execution.get_node_execution(new_step.execution_id)
        if target_execution.branch_id != created_branch.id:
            forked_execution = self._fork_node_execution(
                execution,
                target_execution,
                created_branch.id,
            )
            new_step.execution_id = forked_execution.id
        new_step.parent_step_id = from_step_id
        persisted_step = self.append_step(execution, new_step)
        after_visible_ids = self.get_visible_step_ids(execution)
        removed_visible_step_ids = self.compute_view_diff(
            before_visible_ids,
            after_visible_ids,
        )
        return history_models.HistoryMutationResult(
            changed=(before_visible_ids != after_visible_ids) or True,
            active_branch_id=execution.active_branch_id,
            created_branch_id=created_branch.id,
            branch_head_step_id=persisted_step.id,
            resume_step_id=persisted_step.id,
            removed_visible_step_ids=removed_visible_step_ids,
            upserted_steps=[persisted_step],
        )

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
        branch = execution.get_branch(branch_id)
        execution.active_branch_id = branch.id
        execution.refresh_visible_step_ids()
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
            branch_head_step_id=branch.head_step_id,
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
        replacement_message = state.Message(
            role=message.role,
            text=text,
            thinking_content=message.thinking_content,
        )
        self.add_message(execution, replacement_message)
        replacement_step = state.Step(
            workflow_execution=execution,
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
        return self.fork_from_step(
            execution,
            target_step.parent_step_id,
            replacement_step,
            base_step_id=target_step.id,
        )
