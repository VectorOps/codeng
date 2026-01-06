from __future__ import annotations

from vocode import models as models_mod
from vocode import state as state_mod
from vocode.runner.executors.llm.preprocessors import base as pre_base
from vocode.runner.executors.llm.preprocessors import string_inject as si_mod  # noqa: F401


def test_string_inject_registers_with_factory():
    pre = pre_base.PreprocessorFactory.get("string_inject")
    assert pre is not None
    assert pre.name == "string_inject"


def test_string_inject_creates_new_system_message():
    spec = models_mod.PreprocessorSpec(
        name="string_inject",
        options={"text": "hello", "separator": "\n--\n"},
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages: list[state_mod.Message] = []
    project = object()
    result = pre_base.apply_preprocessors([spec], project, messages)
    assert len(result) == 1
    msg = result[0]
    assert msg.role == models_mod.Role.SYSTEM
    assert msg.text == "hello"


def test_string_inject_prepends_into_existing_user_message():
    spec = models_mod.PreprocessorSpec(
        name="string_inject",
        options={"text": "prefix", "separator": " | "},
        mode=models_mod.Role.USER,
        prepend=True,
    )
    messages = [
        state_mod.Message(role=models_mod.Role.USER, text="base user"),
    ]
    project = object()
    result = pre_base.apply_preprocessors([spec], project, list(messages))
    assert len(result) == 1
    msg = result[0]
    assert msg.role == models_mod.Role.USER
    assert msg.text.startswith("prefix")
    assert msg.text.endswith("base user")
    assert " | " in msg.text


def test_string_inject_is_idempotent():
    spec = models_mod.PreprocessorSpec(
        name="string_inject",
        options={"text": "once", "separator": "\n\n"},
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages: list[state_mod.Message] = []
    project = object()
    result1 = pre_base.apply_preprocessors([spec], project, messages)
    result2 = pre_base.apply_preprocessors([spec], project, result1)
    assert len(result1) == 1
    assert len(result2) == 1
    assert result1[0].text == result2[0].text