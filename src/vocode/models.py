from typing import List, Tuple, Dict, Set, Optional, Type, ClassVar, Any
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    AliasChoices,
)
from enum import Enum
import re

EDGE_ALT_SYNTAX_RE = re.compile(
    r"^\s*([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]+)\s*->\s*([A-Za-z0-9_\-]+)(?::([A-Za-z0-9_\-]+))?\s*$"
)


class OutcomeSlot(BaseModel):
    name: str
    description: Optional[str] = None


class Confirmation(str, Enum):
    # Always request users approval
    MANUAL = "manual"
    # Automatically approve everything
    AUTO = "auto"


class StateResetPolicy(str, Enum):
    # Always reset state when node is re-entered
    RESET = "reset"
    # Keep complete state from previous run
    KEEP = "keep"


class ResultMode(str, Enum):
    # Only pass final response
    FINAL_RESPONSE = "final_response"
    # Pass all complete messages
    ALL_MESSAGES = "all_messages"
    # Concatenate final message with the input message
    CONCATENATE_FINAL = "concatenate_final"


class OutputMode(str, Enum):
    SHOW = "show"
    HIDE_ALL = "hide_all"
    HIDE_FINAL = "hide_final"


class Role(str, Enum):
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class OutcomeStrategy(str, Enum):
    """
    Node outcome strategy. Either a marker in the output (TAG) or an expected function call (FUNCTION)
    """

    TAG = "tag"
    FUNCTION = "function"


class PreprocessorSpec(BaseModel):
    name: str
    options: Dict[str, Any] = Field(default_factory=dict)
    mode: Role = Field(
        default=Role.SYSTEM,
        description="Where to apply this preprocessor: system or a user message",
    )
    prepend: bool = Field(
        default=False,
        description="If true, preprocessor output is prepended to the target text; otherwise the preprocessor transforms/appends.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        # Accept either:
        # - "name" (string) -> defaults to Mode.System
        # - {"name": "name", "options": {...}, "mode": "system"|"user"|Mode}
        if isinstance(v, str):
            return {"name": v, "options": {}, "mode": Role.SYSTEM, "prepend": False}
        if isinstance(v, dict):
            name = v.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    "Preprocessor spec mapping must include non-empty 'name'"
                )
            options = v.get("options", {})
            if options is None:
                options = {}
            if not isinstance(options, dict):
                raise TypeError(
                    "Preprocessor 'options' must be a mapping/dict if provided"
                )
            raw_mode = v.get("mode", Role.SYSTEM)
            # Normalize 'mode' accepting either enum or string (case-insensitive)
            if isinstance(raw_mode, str):
                low = raw_mode.strip().lower()
                if low == "system":
                    mode = Role.SYSTEM
                elif low == "user":
                    mode = Role.USER
                else:
                    raise ValueError("Preprocessor 'mode' must be 'system' or 'user'")
            elif isinstance(raw_mode, Role):
                mode = raw_mode
            else:
                # Allow None -> default
                mode = Role.SYSTEM
            prepend_flag = bool(v.get("prepend", False))
            return {
                "name": name,
                "options": options,
                "mode": mode,
                "prepend": prepend_flag,
            }
        raise TypeError(
            "Preprocessor spec must be a string or a mapping with 'name', optional 'options', and optional 'mode'"
        )


class Node(BaseModel):
    name: str = Field(..., description="Unique node name")
    type: str = Field(..., description="Node type identifier")
    description: Optional[str] = Field(
        None, description="Node description for UI display"
    )
    outcomes: List[OutcomeSlot] = Field(default_factory=list)
    skip: bool = Field(
        default=False,
        description="If true, runner will skip executing this node and suppress notifications; executor instance may still be created.",
    )
    max_runs: Optional[int] = Field(
        default=None,
        description="Maximum number of times this node may execute within a single runner session. None = unlimited; 0 = equivalent to skip=True.",
    )
    message_mode: ResultMode = Field(
        default=ResultMode.FINAL_RESPONSE,
        description=(
            "How to pass messages to the next node.\n"
            "- 'final_response' (default): pass only the final executor message.\n"
            "- 'all_messages': pass all messages from this step (initial inputs + interim + final).\n"
            "- 'concatenate_final': concatenate input message(s) with the final output into a single message."
        ),
    )
    output_mode: OutputMode = Field(
        default=OutputMode.SHOW,
        description=(
            "How this node's output messages are shown in the UI. "
            "'show' displays all messages; 'hide_all' hides all messages; "
            "'hide_final' hides only the final message."
        ),
    )
    confirmation: Confirmation = Field(
        default=Confirmation.MANUAL,
        description="How to handle node final confirmation ",
    )
    reset_policy: StateResetPolicy = Field(
        default=StateResetPolicy.RESET,
        description="Defines how executor state is handled.",
    )

    # Registry keyed by the existing "type" field value
    _registry: ClassVar[Dict[str, Type["Node"]]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Prefer direct class attribute (works reliably for runtime-defined plugins)
        default_type = getattr(cls, "type", None)
        if isinstance(default_type, str) and default_type:
            Node._registry[default_type] = cls  # type: ignore[assignment]
            return

    @field_validator("outcomes", mode="after")
    @classmethod
    def _unique_outcome_names(cls, v: List[OutcomeSlot]) -> List[OutcomeSlot]:
        names = [s.name for s in v]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate outcome slot names found in node.outcomes")
        return v

    @classmethod
    def from_node(cls, data):
        if not isinstance(data, dict):
            raise ValueError(f"Invalid value type for from_node: {data}")

        type_key = data.get("type")
        model_cls = cls._registry.get(type_key)
        if model_cls is None:
            raise ValueError(f"Unknown type: {type_key}")

        return model_cls.model_validate(data)


class Edge(BaseModel):
    source_node: str = Field(..., description="Name of the source node")
    source_outcome: str = Field(
        ..., description="Name of the outcome slot on the source node"
    )
    target_node: str = Field(..., description="Name of the target node")
    reset_policy: Optional[StateResetPolicy] = Field(
        default=None,
        description="Optional reset policy override applied when traversing this edge.",
    )

    @model_validator(mode="before")
    @classmethod
    def _parse_alt_syntax(cls, v: Any) -> Any:
        # Accept "source.node -> target[:reset_policy]" string form
        if isinstance(v, str):
            m = EDGE_ALT_SYNTAX_RE.match(v)
            if not m:
                raise ValueError(
                    "Edge string must be '<source_node>.<source_outcome> -> <target_node>[:<reset_policy>]'"
                )
            data = {
                "source_node": m.group(1),
                "source_outcome": m.group(2),
                "target_node": m.group(3),
            }
            rp = m.group(4)
            if rp:
                data["reset_policy"] = rp  # Parsed by pydantic into ResetPolicy
            return data
        return v


class Graph(BaseModel):
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)

    @property
    def node_by_name(self) -> Dict[str, "Node"]:
        return {n.name: n for n in self.nodes}

    def _children_names(self, node_name: str) -> List[str]:
        return [e.target_node for e in self.edges if e.source_node == node_name]

    @model_validator(mode="after")
    def _validate_graph(self) -> "Graph":
        nodes: List[Node] = self.nodes or []
        edges: List[Edge] = self.edges or []

        node_by_name: Dict[str, Node] = {n.name: n for n in nodes}
        if len(node_by_name) != len(nodes):
            raise ValueError("Duplicate node names detected in graph.nodes")

        declared_outcomes: Set[Tuple[str, str]] = {
            (n.name, slot.name) for n in nodes for slot in n.outcomes
        }

        edges_by_source: Dict[Tuple[str, str], Edge] = {}
        for e in edges:
            if e.source_node not in node_by_name:
                raise ValueError(
                    f"Edge source_node '{e.source_node}' does not exist in graph.nodes"
                )
            if e.target_node not in node_by_name:
                raise ValueError(
                    f"Edge target_node\n'{e.target_node}' does not exist in graph.nodes"
                )

            source_node = node_by_name[e.source_node]
            if e.source_outcome not in {s.name for s in source_node.outcomes}:
                raise ValueError(
                    f"Edge references unknown source_outcome '{e.source_outcome}' on node '{e.source_node}'"
                )

            key = (e.source_node, e.source_outcome)
            if key in edges_by_source:
                raise ValueError(
                    f"Multiple edges found from the same outcome slot: node='{e.source_node}', slot='{e.source_outcome}'"
                )
            edges_by_source[key] = e

        sources_with_edges = set(edges_by_source.keys())
        missing = declared_outcomes - sources_with_edges
        extra = sources_with_edges - declared_outcomes

        if missing or extra:
            msgs = []
            if missing:
                msgs.append(
                    "Missing edges for declared outcome slots: "
                    + ", ".join(
                        [
                            f"{n}:{s}"
                            for (n, s) in sorted(missing, key=lambda x: (x[0], x[1]))
                        ]
                    )
                )
            if extra:
                msgs.append(
                    "Edges originate from undeclared outcome slots: "
                    + ", ".join(
                        [
                            f"{n}:{s}"
                            for (n, s) in sorted(extra, key=lambda x: (x[0], x[1]))
                        ]
                    )
                )
            raise ValueError("; ".join(msgs))

        return self
