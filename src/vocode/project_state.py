from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel


@dataclasses.dataclass
class ProjectAutoApproveRule:
    key: str
    pattern: str

    def matches(self, arguments: Dict[str, Any]) -> bool:
        from vocode.lib import validators

        value = validators.get_value_by_dotted_key(arguments, self.key)
        if value is None:
            return False
        return validators.regex_matches_value(self.pattern, value)


@dataclasses.dataclass
class ProjectAutoApproveToolPolicy:
    tool_name: str
    approve_all: bool = True
    rules: List[ProjectAutoApproveRule] = dataclasses.field(default_factory=list)

    def matches(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        if tool_name != self.tool_name:
            return False
        if self.approve_all:
            return True
        for rule in self.rules:
            if rule.matches(arguments):
                return True
        return False


@dataclasses.dataclass
class ProjectAutoApproveState:
    policies_by_tool: Dict[str, ProjectAutoApproveToolPolicy] = dataclasses.field(
        default_factory=dict
    )

    def should_auto_approve(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        policy = self.policies_by_tool.get(tool_name)
        if policy is None:
            return False
        return policy.matches(tool_name, arguments)

    def add_tool(self, tool_name: str, *, approve_all: bool = True) -> None:
        self.policies_by_tool[tool_name] = ProjectAutoApproveToolPolicy(
            tool_name=tool_name,
            approve_all=approve_all,
        )

    def add_rule(self, tool_name: str, *, key: str, pattern: str) -> None:
        policy = self.policies_by_tool.get(tool_name)
        if policy is None:
            policy = ProjectAutoApproveToolPolicy(
                tool_name=tool_name, approve_all=False
            )
            self.policies_by_tool[tool_name] = policy
        policy.rules.append(ProjectAutoApproveRule(key=key, pattern=pattern))

    def remove_tool(self, tool_name: str) -> bool:
        return self.policies_by_tool.pop(tool_name, None) is not None

    def clear(self) -> None:
        self.policies_by_tool.clear()


class ProjectState:
    """
    Ephemeral, process-local project-level state shared across executors.
    Not persisted across runs.
    """

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self.autoapprove: ProjectAutoApproveState = ProjectAutoApproveState()

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class FileChangeType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class FileChangeModel(BaseModel):
    type: FileChangeType
    # Relative filename within the project root
    relative_filename: str
