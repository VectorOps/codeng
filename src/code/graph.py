from typing import List, Dict, Optional
from .models import Node, Edge, OutcomeSlot, Graph


class RuntimeNode:
    def __init__(self, model: "Node"):
        self._model = model
        self._children: List["RuntimeNode"] = []
        self._child_by_outcome: Dict[str, "RuntimeNode"] = {}

    def get_child_by_outcome(self, outcome_name: str) -> Optional["RuntimeNode"]:
        return self._child_by_outcome.get(outcome_name)

    @property
    def name(self) -> str:
        return self._model.name

    @property
    def model(self) -> "Node":
        return self._model

    @property
    def type(self) -> str:
        return self._model.type

    @property
    def outcomes(self) -> List["OutcomeSlot"]:
        return self._model.outcomes

    @property
    def children(self) -> List["RuntimeNode"]:
        return self._children


class RuntimeGraph:
    def __init__(self, graph: "Graph"):
        if not graph.nodes:
            raise ValueError("RuntimeGraph requires a Graph with at least one node")
        self._graph = graph
        self._runtime_nodes: Dict[str, RuntimeNode] = {
            n.name: RuntimeNode(n) for n in graph.nodes
        }
        # Wire direct children references
        for e in graph.edges:
            parent = self._runtime_nodes[e.source_node]
            child = self._runtime_nodes[e.target_node]
            parent._children.append(child)
            parent._child_by_outcome[e.source_outcome] = child
        self._root: RuntimeNode = self._runtime_nodes[graph.nodes[0].name]

    @property
    def graph(self) -> "Graph":
        return self._graph

    @property
    def root(self) -> "RuntimeNode":
        return self._root

    def get_runtime_node_by_name(self, name: str) -> Optional["RuntimeNode"]:
        return self._runtime_nodes.get(name)


def build(nodes: List["Node"], edges: List["Edge"]) -> RuntimeGraph:
    # Coerce/dispatch any raw node mappings into Node instances
    coerced_nodes = [Node.from_obj(n) for n in nodes]
    graph = Graph(nodes=coerced_nodes, edges=edges)  # triggers Pydantic validation
    return RuntimeGraph(graph)
