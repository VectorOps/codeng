from __future__ import annotations

from vocode import models as models_mod
from vocode import state as state_mod
from vocode.patch import get_supported_formats, get_system_instruction
from vocode.runner.executors.llm.preprocessors import diff as diff_mod
from vocode.runner.executors.llm.preprocessors import base as pre_base


def test_diff_preprocessor_registers_with_factory():
    pre = pre_base.PreprocessorFactory.get("diff")
    assert pre is not None
    assert pre.name == "diff"
    assert "diff patches" in pre.description


def test_diff_preprocessor_applies_instruction_system_mode_prepend():
    fmt = next(iter(get_supported_formats()))
    instruction = get_system_instruction(fmt)

    spec = models_mod.PreprocessorSpec(
        name="diff",
        options={"format": fmt, "suffix": "\n\n"},
        mode=models_mod.Role.SYSTEM,
        prepend=True,
    )
    messages = [
        state_mod.Message(role=models_mod.Role.SYSTEM, text="base system"),
        state_mod.Message(role=models_mod.Role.USER, text="user text"),
    ]

    project = object()
    result = pre_base.apply_preprocessors([spec], project, list(messages))
    assert len(result) == 2
    system_msg = result[0]
    assert system_msg.role == models_mod.Role.SYSTEM
    assert system_msg.text.startswith(instruction)
    assert "base system" in system_msg.text


def test_diff_preprocessor_applies_instruction_user_mode_append():
    fmt = next(iter(get_supported_formats()))
    instruction = get_system_instruction(fmt)

    spec = models_mod.PreprocessorSpec(
        name="diff",
        options={"format": fmt, "suffix": " "},
        mode=models_mod.Role.USER,
        prepend=False,
    )
    messages = [
        state_mod.Message(role=models_mod.Role.SYSTEM, text="base system"),
        state_mod.Message(role=models_mod.Role.USER, text="user text"),
    ]

    project = object()
    result = pre_base.apply_preprocessors([spec], project, list(messages))
    assert len(result) == 2
    user_msg = result[1]
    assert user_msg.role == models_mod.Role.USER
    assert user_msg.text.startswith("user text")
    assert user_msg.text.endswith(instruction)


def test_diff_preprocessor_is_idempotent_for_same_instruction():
    fmt = next(iter(get_supported_formats()))
    instruction = get_system_instruction(fmt)

    spec = models_mod.PreprocessorSpec(
        name="diff",
        options={"format": fmt},
        mode=models_mod.Role.SYSTEM,
        prepend=True,
    )
    messages = [
        state_mod.Message(
            role=models_mod.Role.SYSTEM,
            text=f"{instruction}\nbase system",
        ),
    ]

    project = object()
    result = pre_base.apply_preprocessors([spec], project, list(messages))
    assert len(result) == 1
    # Text should be unchanged because instruction already present
    assert result[0].text == messages[0].text