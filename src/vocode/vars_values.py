from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel


class VarValueChoice(BaseModel):
    name: str
    value: Any


VarTypeValuesProvider = Callable[[str], List[VarValueChoice]]


class VarTypeValuesProviderRegistry:
    def __init__(self) -> None:
        self._providers: Dict[str, VarTypeValuesProvider] = {}

    def register(self, var_type: str, provider: VarTypeValuesProvider) -> None:
        if not isinstance(var_type, str) or not var_type:
            raise ValueError("var_type must be a non-empty string")
        if var_type in self._providers:
            raise ValueError(f"Provider already registered for type {var_type!r}")
        self._providers[var_type] = provider

    def list_values(self, var_type: str, needle: str) -> List[VarValueChoice]:
        provider = self._providers.get(var_type)
        if provider is None:
            return []
        return provider(needle)


_DEFAULT_REGISTRY: Optional[VarTypeValuesProviderRegistry] = None


def get_default_registry() -> VarTypeValuesProviderRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        reg = VarTypeValuesProviderRegistry()
        from vocode.settings.providers import (
            register_default_var_value_providers,
        )

        register_default_var_value_providers(reg)
        _DEFAULT_REGISTRY = reg
    return _DEFAULT_REGISTRY


def register_var_type_values_provider(
    var_type: str, provider: VarTypeValuesProvider
) -> None:
    get_default_registry().register(var_type, provider)


def list_var_type_values(var_type: str, needle: str = "") -> List[VarValueChoice]:
    return get_default_registry().list_values(var_type, needle)
