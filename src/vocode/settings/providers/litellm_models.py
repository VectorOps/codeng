from __future__ import annotations

from vocode import vars_values as vars_values_mod


def _llm_models_provider(needle: str) -> list[vars_values_mod.VarValueChoice]:
    import litellm

    needle_norm = (needle or "").casefold()
    models = sorted(set(str(m) for m in (litellm.model_list or [])))
    if needle_norm:
        models = [m for m in models if needle_norm in m.casefold()]
    return [vars_values_mod.VarValueChoice(name=m, value=m) for m in models]


def register_var_value_providers(
    registry: vars_values_mod.VarTypeValuesProviderRegistry,
) -> None:
    registry.register("llm_models", _llm_models_provider)
