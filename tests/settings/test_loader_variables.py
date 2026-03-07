from pathlib import Path
import os

import pytest

from vocode.settings.loader import load_settings
from vocode.runner.executors.llm.models import LLMNode
from vocode import vars_values as vars_values_mod


def _write_tmp(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_pure_variable_field_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_HOST", "env-host")
    cfg = """
variables:
  HOST: localhost
  ENV_HOST: ${env:TEST_HOST}
internal_http:
  host: ${HOST}
  port: 8080
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.host == "localhost"
    assert settings.internal_http.port == 8080


def test_interpolated_string_resolution_and_var_update_propagation(
    tmp_path: Path,
) -> None:
    cfg = """
variables:
  NAME: world
internal_http:
  host: "hello ${NAME}"
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.host == "hello world"

    settings.set_variable_value("NAME", "friend")
    assert settings.internal_http.host == "hello friend"


def test_multiple_interpolated_variables(tmp_path: Path) -> None:
    cfg = """
variables:
  A: one
  B: two
internal_http:
  host: "${A}-${B}-${A}"
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.host == "one-two-one"


def test_workflow_llm_node_model_variable_resolution(tmp_path: Path) -> None:
    cfg = """
variables:
  LLM_MODEL: gpt-4o
workflows:
  wf:
    nodes:
      - name: llm-node
        type: llm
        model: ${LLM_MODEL}
    edges: []
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert "wf" in settings.workflows
    wf = settings.workflows["wf"]
    assert wf.nodes
    node = wf.nodes[0]
    assert node.type == "llm"
    assert getattr(node, "model", None) == "gpt-4o"


def test_runtime_var_update_updates_workflow_llm_node_fields(tmp_path: Path) -> None:
    cfg = """
variables:
  LLM_MODEL: gpt-4o
workflows:
  wf:
    nodes:
      - name: llm-node
        type: llm
        model: ${LLM_MODEL}
    edges: []
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    node = settings.workflows["wf"].nodes[0]
    assert getattr(node, "model", None) == "gpt-4o"

    settings.set_variable_value("LLM_MODEL", "gpt-4.1")
    assert getattr(node, "model", None) == "gpt-4.1"


def test_object_mode_variable_definition_with_value(tmp_path: Path) -> None:
    cfg = """
variables:
  HOST:
    value: localhost
    options: [localhost, remote]
internal_http:
  host: ${HOST}
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.host == "localhost"


def test_object_mode_variable_definition_without_explicit_value(tmp_path: Path) -> None:
    cfg = """
variables:
  PORT:
    value: 9000
    lookup: ports
internal_http:
  port: ${PORT}
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.port == 9000


def test_env_var_used_in_typed_int_field(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_PORT", "9000")
    cfg = """
variables:
  PORT: ${env:TEST_PORT}
internal_http:
  port: ${PORT}
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    assert settings.internal_http is not None
    assert settings.internal_http.port == 9000


def test_variable_definition_with_type_field_and_choices(tmp_path: Path) -> None:
    cfg = """
variables:
  LLM_MODEL:
    value: gpt-4o
    type: llm_models
workflows:
  wf:
    nodes:
      - name: llm-node
        type: llm
        model: ${LLM_MODEL}
    edges: []
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    var_def = settings.get_variable_def("LLM_MODEL")
    assert var_def is not None
    assert var_def.type == "llm_models"

    choices = settings.list_variable_value_choices("LLM_MODEL", needle="gpt")
    assert choices
    assert all(isinstance(c, vars_values_mod.VarValueChoice) for c in choices)


def test_variable_value_choices_from_explicit_options(tmp_path: Path) -> None:
    cfg = """
variables:
  HOST:
    value: localhost
    options: [localhost, remote]
internal_http:
  host: ${HOST}
"""
    path = _write_tmp(tmp_path, cfg)
    settings = load_settings(str(path))

    choices = settings.list_variable_value_choices("HOST", needle="rem")
    assert [c.value for c in choices] == ["remote"]
