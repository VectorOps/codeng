from __future__ import annotations

from typing import List

from vocode import state


class DummyNode:
    def __init__(self) -> None:
        self.collapse = None
        self.collapse_lines = None
        self.visible = True
        self.tool_collapse = None


class DummyGraph:
    def __init__(self, node_names: List[str]) -> None:
        self.node_by_name = {name: DummyNode() for name in node_names}


class DummyWorkflow:
    def __init__(self, node_names: List[str]) -> None:
        self.graph = DummyGraph(node_names)


class DummyRunnerWithWorkflow:
    def __init__(
        self,
        node_names: List[str],
        execution: state.WorkflowExecution | None = None,
        status: state.RunnerStatus = state.RunnerStatus.RUNNING,
    ) -> None:
        self.workflow = DummyWorkflow(node_names)
        self.execution = execution or state.WorkflowExecution(workflow_name="dummy")
        self.status = status

    @property
    def input_workflow_id(self) -> str:
        return str(self.execution.id)
