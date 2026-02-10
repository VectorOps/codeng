from pathlib import Path
import os

from vocode.settings.loader import load_settings
from vocode.runner.executors.llm.models import LLMNode


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


def test_interpolated_string_resolution_and_assignment(tmp_path: Path) -> None:
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

    settings.internal_http.host = "plain"
    assert settings.internal_http.host == "plain"


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
