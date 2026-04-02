from __future__ import annotations

from vocode import vars_values as vars_values_mod


def register_default_var_value_providers(
    registry: vars_values_mod.VarTypeValuesProviderRegistry,
) -> None:
    from . import connect_models

    connect_models.register_var_value_providers(registry)
