from typing import Optional, Dict, AsyncIterator
from . import models, state
from .project import Project
from .graph import RuntimeGraph
from .base import BaseExecutor


class Runner:
    def __init_(
        self, workflow, project: Project, initial_message: Optional[state.Message]
    ):
        self.workflow = workflow
        self.project = project
        self.initial_message = initial_message

        self.status = state.RunnerStatus.IDLE
        self.graph = RuntimeGraph(workflow.graph)
        self.run = state.WorkflowExecution(workflow_name=workflow.name)

        self._executors: Dict[str, BaseExecutor] = {
            n.name: BaseExecutor.create_for_node(n, project=self.project)
            for n in self.workflow.graph.nodes
        }

    def run(self) -> AsyncIterator[RunEvent]:
        if self.status not in (state.RunnerStatus.IDLE, state.RunnerStatus.STOPPED):
            raise RuntimeError(
                f"run() not allowed when runner status is '{self.status}'. Allowed: 'idle', 'stopped'"
            )

        self.status = state.RunnerStatus.RUNNING
        self.state.status = state.RunStatus.RUNNING

        # Figure out if we start from beginning or continuing with an existing execution
        if self.state.steps:
            step = self.state.steps[-1]
            current_node = self._find_node_by_name(step.node.execution.node)
        else:
            current_node = self.graph.root

        # Main loop
        while True:
            pass
