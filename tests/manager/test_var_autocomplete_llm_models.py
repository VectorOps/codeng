from __future__ import annotations

import types

import pytest

from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer
from vocode.manager import autocomplete_providers as autocomplete_providers
from vocode import settings as vocode_settings
from vocode.vars import VarDef
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_var_autocomplete_llm_models_uses_raw_value_as_insert_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = types.SimpleNamespace(model_list=["gpt-4o", "gpt-3.5-turbo"])
    monkeypatch.setitem(__import__("sys").modules, "litellm", stub)

    settings = vocode_settings.Settings()
    settings.set_var_context({"LLM_MODEL": "gpt-4o"})
    settings._set_var_defs(
        {
            "LLM_MODEL": VarDef(
                value="gpt-4o",
                type="llm_models",
                options=None,
            )
        }
    )
    project = StubProject(settings=settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.var_autocomplete_provider(
        server,
        "/var set LLM_MODEL gpt",
        0,
        len("/var set LLM_MODEL gpt"),
    )
    assert items is not None
    assert items
    assert all(it.insert_text == it.title for it in items)
