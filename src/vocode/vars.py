from __future__ import annotations

from typing import Any, List, Tuple, Optional, Dict
import json
import os
import re

from pydantic import BaseModel, PrivateAttr, model_validator


VAR_PATTERN = re.compile(
    r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?)\}"
)


class VarDef(BaseModel):
    value: Any = None
    options: Optional[List[Any]] = None
    lookup: Optional[str] = None

    @model_validator(mode="after")
    def _validate_mutual_exclusive(self) -> "VarDef":
        if self.options is not None and self.lookup is not None:
            raise ValueError("'options' and 'lookup' cannot both be set")
        return self

    @classmethod
    def from_raw(cls, raw: Any) -> "VarDef":
        if isinstance(raw, dict):
            if any(k in raw for k in ("value", "options", "lookup")):
                data: Dict[str, Any] = {"value": raw.get("value")}
                if "options" in raw:
                    data["options"] = raw["options"]
                if "lookup" in raw:
                    data["lookup"] = raw["lookup"]
                return cls.model_validate(data)
        return cls(value=raw)


class VarEnv:
    def __init__(self, vars_map: Dict[str, Any]) -> None:
        self._vars_map = vars_map

    @property
    def vars_map(self) -> Dict[str, Any]:
        return self._vars_map

    def lookup(self, name: str) -> tuple[bool, Any]:
        if name.startswith("env:"):
            env_name = name[4:]
            if not env_name:
                return False, None
            val = os.getenv(env_name)
            if val is None:
                return False, None
            return True, val

        if name in self._vars_map:
            val = self._vars_map[name]
            if isinstance(val, VarDef):
                return True, val.value
            return True, val

        return False, None

    def resolve_placeholder(self, name: str) -> str:
        found, val = self.lookup(name)
        if not found:
            return "${" + name + "}"
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    def interpolate(self, template: str) -> str:
        def repl(m: re.Match) -> str:
            return self.resolve_placeholder(m.group(1))

        interpolated = VAR_PATTERN.sub(repl, template)
        return interpolated.replace("$${", "${")


class VarExpr:
    def resolve(self) -> Any:
        raise NotImplementedError

    def assign(self, owner: Any, field_name: str, value: Any) -> None:
        raise NotImplementedError


class VarRef(VarExpr):
    def __init__(self, env: VarEnv, name: str) -> None:
        self._env = env
        self._name = name

    def resolve(self) -> Any:
        found, val = self._env.lookup(self._name)
        if not found:
            return "${" + self._name + "}"
        return val

    def __repr__(self) -> str:
        return f"VarRef({self._name!r})"

    def assign(self, owner: Any, field_name: str, value: Any) -> None:
        self._env.vars_map[self._name] = value


class VarInterpolated(VarExpr):
    def __init__(self, env: VarEnv, template: str) -> None:
        self._env = env
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

    def resolve(self) -> str:
        out: List[str] = []
        for kind, payload in self._parts:
            if kind == "text":
                out.append(payload)
            else:
                out.append(self._env.resolve_placeholder(payload))
        result = "".join(out)
        return result.replace("$${", "${")

    def __repr__(self) -> str:
        return f"VarInterpolated({self._template!r})"

    def assign(self, owner: Any, field_name: str, value: Any) -> None:
        raise ValueError(
            "Cannot assign to interpolated variable-backed field; update variables instead"
        )


class BaseVarModel(BaseModel):
    _var_env: Optional[VarEnv] = PrivateAttr(default=None)

    def set_var_context(self, vars_map: Dict[str, Any]) -> None:
        env = VarEnv(vars_map)
        self._var_env = env
        self._propagate_var_context(self, env)

    @classmethod
    def _wrap_value(cls, value: Any, env: VarEnv) -> Any:
        if isinstance(value, str):
            if value.startswith("${") and value.endswith("}") and VAR_PATTERN.fullmatch(value):
                name = value[2:-1]
                return VarRef(env, name)
            if VAR_PATTERN.search(value):
                return VarInterpolated(env, value)
        return value

    @classmethod
    def _propagate_var_context(cls, obj: Any, env: VarEnv) -> None:
        if isinstance(obj, BaseVarModel):
            BaseModel.__setattr__(obj, "_var_env", env)
            for field_name, field_info in obj.model_fields.items():
                value = getattr(obj, field_name)
                wrapped = cls._wrap_container(value, env)
                if wrapped is not value:
                    BaseModel.__setattr__(obj, field_name, wrapped)
                cls._propagate_var_context(value, env)
        elif isinstance(obj, list):
            for item in obj:
                cls._propagate_var_context(item, env)
        elif isinstance(obj, dict):
            for item in obj.values():
                cls._propagate_var_context(item, env)

    @classmethod
    def _wrap_container(cls, value: Any, env: VarEnv) -> Any:
        if isinstance(value, str):
            return cls._wrap_value(value, env)
        if isinstance(value, list):
            return [cls._wrap_container(v, env) for v in value]
        if isinstance(value, dict):
            return {k: cls._wrap_container(v, env) for k, v in value.items()}
        return value

    def __getattribute__(self, name: str) -> Any:
        value = super().__getattribute__(name)
        if isinstance(value, VarExpr):
            return value.resolve()
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            return super().__setattr__(name, value)
        current = self.__dict__.get(name)
        if isinstance(current, VarExpr):
            current.assign(self, name, value)
            return
        return super().__setattr__(name, value)
