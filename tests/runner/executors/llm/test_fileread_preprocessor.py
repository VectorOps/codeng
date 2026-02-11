from __future__ import annotations

from pathlib import Path

from vocode import models as models_mod
from vocode import state as state_mod
from vocode.runner.executors.llm.preprocessors import base as pre_base
from vocode.runner.executors.llm.preprocessors import file_read as fr_mod


class _Project:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path


def test_file_read_registers_with_factory():
    pre = pre_base.PreprocessorFactory.get("file_read")
    assert pre is not None
    assert pre.name == "file_read"


def test_file_read_injects_into_new_system_message(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("content-a", encoding="utf-8")

    project = _Project(base_path=tmp_path)
    spec = models_mod.PreprocessorSpec(
        name="file_read",
        options={"paths": ["a.txt"]},
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages: list[state_mod.Message] = []

    result = pre_base.apply_preprocessors([spec], project, messages)
    assert len(result) == 1
    msg = result[0]
    assert msg.role == models_mod.Role.SYSTEM
    assert "User provided a.txt:" in msg.text
    assert "content-a" in msg.text


def test_file_read_prepends_into_existing_user_message(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("content-b", encoding="utf-8")

    project = _Project(base_path=tmp_path)
    spec = models_mod.PreprocessorSpec(
        name="file_read",
        options={"paths": ["b.txt"], "separator": "\n---\n"},
        mode=models_mod.Role.USER,
        prepend=True,
    )
    messages = [
        state_mod.Message(role=models_mod.Role.USER, text="base user"),
    ]

    result = pre_base.apply_preprocessors([spec], project, list(messages))
    assert len(result) == 1
    msg = result[0]
    assert msg.role == models_mod.Role.USER
    assert msg.text.endswith("base user")
    assert "content-b" in msg.text
    assert "\n---\n" in msg.text


def test_file_read_is_idempotent(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("content-c", encoding="utf-8")

    project = _Project(base_path=tmp_path)
    spec = models_mod.PreprocessorSpec(
        name="file_read",
        options={"paths": ["c.txt"], "separator": "\n\n"},
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages: list[state_mod.Message] = []

    result1 = pre_base.apply_preprocessors([spec], project, messages)
    result2 = pre_base.apply_preprocessors([spec], project, result1)

    assert len(result1) == 1
    assert len(result2) == 1
    assert result1[0].text == result2[0].text


def test_file_read_uses_relative_path_in_template(tmp_path):
    subdir = tmp_path / "src" / "game"
    subdir.mkdir(parents=True)
    f = subdir / "orion2.h"
    f.write_text("content-orion2", encoding="utf-8")

    project = _Project(base_path=tmp_path)
    rel_path = "src/game/orion2.h"
    spec = models_mod.PreprocessorSpec(
        name="file_read",
        options={
            "paths": [rel_path],
            "prepend_template": "User provided {path}:\n",
        },
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages: list[state_mod.Message] = []

    result = pre_base.apply_preprocessors([spec], project, messages)
    assert len(result) == 1
    msg = result[0]
    assert msg.role == models_mod.Role.SYSTEM
    assert f"User provided {rel_path}:" in msg.text
    assert "content-orion2" in msg.text
