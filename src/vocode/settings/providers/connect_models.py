from __future__ import annotations

import connect

from vocode import vars_values as vars_values_mod


def _connect_models_provider(needle: str) -> list[vars_values_mod.VarValueChoice]:
    needle_norm = (needle or "").casefold()
    models = sorted(
        {
            f"{model.provider}/{model.model}"
            for model in connect.default_model_registry.list_models()
        }
    )
    if needle_norm:
        models = [model for model in models if needle_norm in model.casefold()]
    return [vars_values_mod.VarValueChoice(name=model, value=model) for model in models]


def register_var_value_providers(
    registry: vars_values_mod.VarTypeValuesProviderRegistry,
) -> None:
    registry.register("llm_models", _connect_models_provider)