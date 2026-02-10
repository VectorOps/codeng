from __future__ import annotations

from typing import Any, List, Tuple
import json
import os
import re


VAR_PATTERN = re.compile(
    r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?)\}"
)


class VariableExpression:
    def resolve(self, settings: Any) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def assign(self, owner: Any, field_name: str, value: Any) -> None:
        from pydantic import BaseModel

        BaseModel.__setattr__(owner, field_name, value)


def _lookup_var_value(name: str, vars_map: dict[str, Any]) -> tuple[bool, Any]:
    if name.startswith("env:"):
        env_name = name[4:]
        if not env_name:
            return False, None
        val = os.getenv(env_name)
        if val is None:
            return False, None
        return True, val

    if name in vars_map:
        return True, vars_map[name]

    return False, None


def _resolve_placeholder(name: str, vars_map: dict[str, Any]) -> str:
    found, val = _lookup_var_value(name, vars_map)
    if not found:
        return "${" + name + "}"
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


class VariableRef(VariableExpression):
    def __init__(self, settings: Any, name: str) -> None:
        self._settings = settings
        self._name = name

    def resolve(self, settings: Any | None = None) -> Any:
        root = settings or self._settings
        vars_map = getattr(root, "_vars_map", {})
        found, val = _lookup_var_value(self._name, vars_map)
        if not found:
            return "${" + self._name + "}"
        return val

    def __repr__(self) -> str:
        return f"VariableRef({self._name!r})"


class InterpolatedString(VariableExpression):
    def __init__(self, settings: Any, template: str) -> None:
        self._settings = settings
        self._template = template
        self._parts: List[Tuple[str, str]] = []

        last_end = 0
        for m in VAR_PATTERN.finditer(template):
            if m.start() > last_end:
                self._parts.append(("text", template[last_end : m.start()]))
            self._parts.append(("var", m.group(1)))
            last_end = m.end()
        if last_end < len(template):
            self._parts.append(("text", template[last_end:]))

    def resolve(self, settings: Any | None = None) -> str:
        root = settings or self._settings
        vars_map = getattr(root, "_vars_map", {})
        out: List[str] = []
        for kind, payload in self._parts:
            if kind == "text":
                out.append(payload)
            else:
                out.append(_resolve_placeholder(payload, vars_map))
        result = "".join(out)
        return result.replace("$${", "${")

    def __repr__(self) -> str:
        return f"InterpolatedString({self._template!r})"
