from __future__ import annotations

from vocode import models as models_mod
from vocode import state as state_mod
from vocode.runner.executors.llm.preprocessors import base as pre_mod


@pre_mod.PreprocessorFactory.register(
    "test_decorated_preprocessor",
    description="decorated preprocessor",
)
def _decorated_preprocessor(project, spec, messages):
    new_messages = list(messages)
    new_messages.append(
        state_mod.Message(role=models_mod.Role.SYSTEM, text="added")
    )
    return new_messages


def test_preprocessor_factory_decorator_and_apply():
    pre = pre_mod.PreprocessorFactory.get("test_decorated_preprocessor")
    assert pre is not None
    assert pre.name == "test_decorated_preprocessor"
    assert pre.description == "decorated preprocessor"

    project = object()
    spec = models_mod.PreprocessorSpec(name="test_decorated_preprocessor")
    messages: list[state_mod.Message] = []

    direct_result = pre.func(project, spec, messages)
    assert isinstance(direct_result, list)
    assert len(direct_result) == 1
    assert direct_result[0].text == "added"

    applied_result = pre_mod.apply_preprocessors([spec], project, messages)
    assert isinstance(applied_result, list)
    assert len(applied_result) == 1
    assert applied_result[0].text == "added"

    assert (
        pre_mod.PreprocessorFactory.unregister("test_decorated_preprocessor") is True
    )
    assert pre_mod.PreprocessorFactory.get("test_decorated_preprocessor") is None