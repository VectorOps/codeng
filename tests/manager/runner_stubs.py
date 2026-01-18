from __future__ import annotations

from typing import List


class DummyNode:
    def __init__(self) -> None:
        self.collapse = None
        self.collapse_lines = None


class DummyGraph:
    def __init__(self, node_names: List[str]) -> None:
        self.node_by_name = {name: DummyNode() for name in node_names}


class DummyWorkflow:
    def __init__(self, node_names: List[str]) -> None:
        self.graph = DummyGraph(node_names)


class DummyRunnerWithWorkflow:
    def __init__(self, node_names: List[str]) -> None:
        self.workflow = DummyWorkflow(node_names)
