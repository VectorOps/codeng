from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from vocode import models, state
from . import models as history_models


class HistoryManager:
    def list_branch_summaries(
        self,
        execution: state.WorkflowExecution,
    ) -> list[history_models.HistoryBranchSummary]:
        self._ensure_default_branch(execution)
        branches = sorted(
            execution.branches_by_id.values(),
            key=lambda branch: branch.created_at,
        )
        return [
            history_models.HistoryBranchSummary(
                id=branch.id,
                head_step_id=branch.head_step_id,
                base_step_id=branch.base_step_id,
                label=branch.label,
                created_at=branch.created_at,
                is_active=branch.id == execution.active_branch_id,
            )
            for branch in branches
        ]

    def upsert_message(
        self,
        execution: state.WorkflowExecution,
        message: state.Message,
    ) -> state.Message:
        execution.messages_by_id[message.id] = message
        return message

    def create_branch(
        self,
        execution: state.WorkflowExecution,
        *,
        head_step_id: Optional[UUID] = None,
        base_step_id: Optional[UUID] = None,
        label: Optional[str] = None,
        activate: bool = True,
    ) -> state.BranchRecord:
        self._ensure_default_branch(execution)
        branch = state.BranchRecord(
            head_step_id=head_step_id,
            base_step_id=base_step_id,
            label=label,
        )
        execution.branches_by_id[branch.id] = branch
        if activate:
            execution.active_branch_id = branch.id
            self._refresh_step_ids(execution)
        return branch

    def upsert_node_execution(
        self,
        execution: state.WorkflowExecution,
        node_execution: state.NodeExecution,
    ) -> state.NodeExecution:
        self._ensure_default_branch(execution)
        existing = execution.node_executions.get(node_execution.id)
        if existing is not None and not node_execution.step_ids:
            node_execution.step_ids = list(existing.step_ids)
        if node_execution.branch_id is None:
            if existing is not None and existing.branch_id is not None:
                node_execution.branch_id = existing.branch_id
            else:
                node_execution.branch_id = execution.active_branch_id
        node_execution._workflow_execution = execution
        execution.node_executions[node_execution.id] = node_execution
        return node_execution

    def upsert_step(
        self,
        execution: state.WorkflowExecution,
        step: state.Step,
    ) -> state.Step:
        self._ensure_default_branch(execution)
        existing = execution.steps_by_id.get(step.id)
        node_execution = execution.get_node_execution(step.execution_id)
        if node_execution.branch_id is None:
            node_execution.branch_id = execution.active_branch_id
        branch = execution.get_branch(node_execution.branch_id)
        if (
            step.message_id is not None
            and step.message_id not in execution.messages_by_id
        ):
            raise KeyError(f"Unknown message id: {step.message_id}")
        if step.parent_step_id is None and existing is None:
            step.parent_step_id = branch.head_step_id
        if existing is not None:
            if existing.execution_id != step.execution_id:
                old_execution = execution.get_node_execution(existing.execution_id)
                old_execution.step_ids = [
                    step_id for step_id in old_execution.step_ids if step_id != step.id
                ]
            if existing.parent_step_id != step.parent_step_id:
                if existing.parent_step_id is not None:
                    old_parent = execution.steps_by_id.get(existing.parent_step_id)
                    if old_parent is not None:
                        old_parent.child_step_ids = [
                            child_id
                            for child_id in old_parent.child_step_ids
                            if child_id != step.id
                        ]
        if step.parent_step_id is not None:
            parent_step = execution.get_step(step.parent_step_id)
            if step.id not in parent_step.child_step_ids:
                parent_step.child_step_ids.append(step.id)
        step._workflow_execution = execution
        execution.steps_by_id[step.id] = step
        if step.id not in node_execution.step_ids:
            node_execution.step_ids.append(step.id)
        if existing is None or branch.head_step_id == step.id:
            branch.head_step_id = step.id
        if branch.base_step_id is None:
            path = self._get_path_from_head(execution, step.id)
            branch.base_step_id = path[0] if path else step.id
        elif branch.base_step_id == step.id:
            path = self._get_path_from_head(execution, step.id)
            branch.base_step_id = path[0] if path else step.id
        if execution.active_branch_id == branch.id:
            self._refresh_step_ids(execution)
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
                branch.head_step_id = self._find_existing_ancestor(
                    execution,
                    branch.head_step_id,
                    removed_parent_ids,
                )
            if branch.base_step_id in step_ids_set:
                branch.base_step_id = self._find_existing_ancestor(
                    execution,
                    branch.base_step_id,
                    removed_parent_ids,
                )
        self._ensure_default_branch(execution)
        self._refresh_step_ids(execution)

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
        forked_execution = state.NodeExecution(
            workflow_execution=execution,
            node=source_execution.node,
            previous_id=source_execution.previous_id,
            branch_id=branch_id,
            input_message_ids=list(source_execution.input_message_ids),
            status=source_execution.status,
            state=source_execution.state,
        )
        return self.upsert_node_execution(execution, forked_execution)

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
        before_step_ids = execution.get_step_ids()
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
        persisted_step = self.upsert_step(execution, new_step)
        after_step_ids = execution.get_step_ids()
        removed_step_ids = self.compute_removed_step_ids(
            before_step_ids,
            after_step_ids,
        )
        return history_models.HistoryMutationResult(
            changed=True,
            mutation_kind=history_models.HistoryMutationKind.FORK,
            active_branch_id=execution.active_branch_id,
            created_branch_id=created_branch.id,
            removed_step_ids=removed_step_ids,
            upserted_step_ids=[persisted_step.id],
            upserted_steps=[persisted_step],
            branch_summaries=self.list_branch_summaries(execution),
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

    def compute_removed_step_ids(
        self,
        before_step_ids: list[UUID],
        after_step_ids: list[UUID],
    ) -> list[UUID]:
        after_ids = set(after_step_ids)
        return [step_id for step_id in before_step_ids if step_id not in after_ids]

    def switch_branch(
        self,
        execution: state.WorkflowExecution,
        branch_id: UUID,
    ) -> history_models.HistoryMutationResult:
        before_step_ids = execution.get_step_ids()
        branch = execution.get_branch(branch_id)
        execution.active_branch_id = branch.id
        self._refresh_step_ids(execution)
        after_step_ids = execution.get_step_ids()
        removed_step_ids = self.compute_removed_step_ids(
            before_step_ids,
            after_step_ids,
        )
        added_visible_ids = [
            step_id for step_id in after_step_ids if step_id not in set(before_step_ids)
        ]
        return history_models.HistoryMutationResult(
            changed=before_step_ids != after_step_ids,
            mutation_kind=history_models.HistoryMutationKind.SWITCH_BRANCH,
            active_branch_id=branch.id,
            removed_step_ids=removed_step_ids,
            upserted_step_ids=added_visible_ids,
            upserted_steps=[
                execution.get_step(step_id) for step_id in added_visible_ids
            ],
            branch_summaries=self.list_branch_summaries(execution),
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
        self.upsert_message(execution, replacement_message)
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

    def _ensure_default_branch(
        self,
        execution: state.WorkflowExecution,
    ) -> state.BranchRecord:
        if execution.active_branch_id is not None:
            branch = execution.branches_by_id.get(execution.active_branch_id)
            if branch is not None:
                return branch
        if execution.branches_by_id:
            branch = next(iter(execution.branches_by_id.values()))
            execution.active_branch_id = branch.id
            return branch
        ordered_steps = tuple(execution.iter_all_steps())
        head_step_id = ordered_steps[-1].id if ordered_steps else None
        base_step_id = None
        if ordered_steps:
            path = self._get_path_from_head(execution, head_step_id)
            if path:
                base_step_id = path[0]
        branch = state.BranchRecord(
            head_step_id=head_step_id,
            base_step_id=base_step_id,
        )
        execution.branches_by_id[branch.id] = branch
        execution.active_branch_id = branch.id
        return branch

    def _refresh_step_ids(
        self,
        execution: state.WorkflowExecution,
    ) -> None:
        branch = self._ensure_default_branch(execution)
        execution.step_ids = self._compute_branch_step_ids(execution, branch.id)

    def _compute_branch_step_ids(
        self,
        execution: state.WorkflowExecution,
        branch_id: UUID,
    ) -> list[UUID]:
        branch = execution.get_branch(branch_id)
        if branch.head_step_id is None:
            return []
        step_ids: list[UUID] = []
        seen: set[UUID] = set()
        current_step_id: Optional[UUID] = branch.head_step_id
        while current_step_id is not None:
            if current_step_id in seen:
                break
            seen.add(current_step_id)
            step_ids.append(current_step_id)
            current_step_id = execution.get_step(current_step_id).parent_step_id
        step_ids.reverse()
        return step_ids

    def _get_path_from_head(
        self,
        execution: state.WorkflowExecution,
        step_id: Optional[UUID],
    ) -> list[UUID]:
        if step_id is None:
            return []
        path: list[UUID] = []
        seen: set[UUID] = set()
        current_step_id: Optional[UUID] = step_id
        while current_step_id is not None:
            if current_step_id in seen:
                break
            seen.add(current_step_id)
            path.append(current_step_id)
            step = execution.steps_by_id.get(current_step_id)
            if step is None:
                break
            current_step_id = step.parent_step_id
        path.reverse()
        return path

    def _find_existing_ancestor(
        self,
        execution: state.WorkflowExecution,
        step_id: Optional[UUID],
        removed_parent_ids: Optional[dict[UUID, Optional[UUID]]] = None,
    ) -> Optional[UUID]:
        current_step_id = step_id
        while current_step_id is not None:
            step = execution.steps_by_id.get(current_step_id)
            if step is not None:
                return current_step_id
            if removed_parent_ids is None:
                return None
            current_step_id = removed_parent_ids.get(current_step_id)
        return None

    def _normalize_tree_state(
        self,
        execution: state.WorkflowExecution,
    ) -> None:
        has_any_parent = any(
            step.parent_step_id is not None for step in execution.steps_by_id.values()
        )
        ordered_steps = tuple(execution.iter_all_steps())
        if not has_any_parent and ordered_steps:
            previous_step_id: Optional[UUID] = None
            for step in ordered_steps:
                step.parent_step_id = previous_step_id
                previous_step_id = step.id
        child_ids_by_parent: dict[UUID, list[UUID]] = {}
        for step in execution.steps_by_id.values():
            step.child_step_ids = []
        for step in ordered_steps:
            if step.parent_step_id is None:
                continue
            child_ids = child_ids_by_parent.get(step.parent_step_id)
            if child_ids is None:
                child_ids = []
                child_ids_by_parent[step.parent_step_id] = child_ids
            child_ids.append(step.id)
        for parent_step_id, child_ids in child_ids_by_parent.items():
            parent_step = execution.steps_by_id.get(parent_step_id)
            if parent_step is None:
                continue
            parent_step.child_step_ids = child_ids
        branch = self._ensure_default_branch(execution)
        if branch.head_step_id is None and ordered_steps:
            branch.head_step_id = ordered_steps[-1].id
        if branch.base_step_id is None and branch.head_step_id is not None:
            path = self._get_path_from_head(execution, branch.head_step_id)
            if path:
                branch.base_step_id = path[0]
        for node_execution in execution.node_executions.values():
            if node_execution.branch_id is None:
                node_execution.branch_id = branch.id
