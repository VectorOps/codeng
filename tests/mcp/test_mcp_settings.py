from pathlib import Path

import pytest

from vocode import error_reporting
from vocode.runner.executors.llm.models import LLMNode
from vocode.settings.loader import load_settings
from vocode.settings import MCPExternalSourceSettings, MCPStdioSourceSettings


def _write_tmp(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _assert_config_error(tmp_path: Path, cfg: str, expected: str) -> None:
    with pytest.raises(error_reporting.ConfigLoadError) as exc_info:
        load_settings(str(_write_tmp(tmp_path, cfg)))

    error = exc_info.value
    assert error.details is not None
    assert expected in error.details


def test_loads_mcp_settings_and_node_selectors(tmp_path: Path) -> None:
    cfg = """
mcp:
  hide_listed_tools: true
  discovery:
    max_results: 5
  protocol:
    request_timeout_s: 20
  sources:
    local:
      kind: stdio
      command: uvx
      args: [mcp-server]
    remote:
      kind: external
      url: https://example.com/mcp
workflows:
  demo:
    nodes:
      - type: llm
        name: assistant
        model: test-model
        mcp:
          hide_listed_tools: true
          resolution_mode: inject
          tools:
            - source: local
              tool: "*"
          disabled_tools:
            - source: local
              tool: dangerous
    edges: []
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    assert settings.mcp.hide_listed_tools is True
    assert settings.mcp.discovery is not None
    assert settings.mcp.discovery.max_results == 5
    assert settings.mcp.protocol is not None
    assert settings.mcp.protocol.request_timeout_s == 20
    assert isinstance(settings.mcp.sources["local"], MCPStdioSourceSettings)
    assert isinstance(settings.mcp.sources["remote"], MCPExternalSourceSettings)

    node = settings.workflows["demo"].nodes[0]
    assert isinstance(node, LLMNode)
    assert node.mcp is not None
    assert node.mcp.hide_listed_tools is True
    assert node.mcp.resolution_mode == "inject"
    assert [(item.source, item.tool) for item in node.mcp.tools] == [("local", "*")]
    assert [(item.source, item.tool) for item in node.mcp.disabled_tools] == [
        ("local", "dangerous")
    ]


def test_resolves_variables_in_mcp_source_fields(tmp_path: Path) -> None:
    cfg = """
variables:
  MCP_CMD: uvx
  MCP_HOST: mcp.example.com
mcp:
  sources:
    local:
      kind: stdio
      command: ${MCP_CMD}
      args: [server]
    remote:
      kind: external
      url: https://${MCP_HOST}/rpc
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    local = settings.mcp.sources["local"]
    remote = settings.mcp.sources["remote"]
    assert isinstance(local, MCPStdioSourceSettings)
    assert isinstance(remote, MCPExternalSourceSettings)
    assert local.command == "uvx"
    assert remote.url == "https://mcp.example.com/rpc"


def test_rejects_invalid_protocol_timeout_bounds(tmp_path: Path) -> None:
    cfg = """
mcp:
  protocol:
    request_timeout_s: 30
    max_request_timeout_s: 20
"""

    _assert_config_error(tmp_path, cfg, "max_request_timeout_s")


def test_rejects_empty_mcp_tool_selector_fields(tmp_path: Path) -> None:
    cfg = """
workflows:
  demo:
    nodes:
      - type: llm
        name: assistant
        model: test-model
        mcp:
          tools:
            - source: ""
              tool: search
    edges: []
"""

    _assert_config_error(tmp_path, cfg, "source must be non-empty")


def test_rejects_workflow_level_mcp_config(tmp_path: Path) -> None:
    cfg = """
workflows:
  demo:
    mcp:
      tools:
        - source: local
          tool: "*"
    nodes: []
    edges: []
"""

    _assert_config_error(tmp_path, cfg, "workflow-level mcp config")


def test_normalizes_path_roots_and_deduplicates_entries(tmp_path: Path) -> None:
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()
    cfg = f"""
mcp:
  roots:
    entries:
      - path: {root_dir}
      - uri: {root_dir.resolve().as_uri()}
  sources:
    local:
      kind: stdio
      command: uvx
      roots:
        entries:
          - path: {root_dir}
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    assert settings.mcp.roots is not None
    assert len(settings.mcp.roots.entries) == 1
    assert settings.mcp.roots.entries[0].uri == root_dir.resolve().as_uri()

    source = settings.mcp.sources["local"]
    assert isinstance(source, MCPStdioSourceSettings)
    assert source.roots is not None
    assert source.roots.entries[0].uri == root_dir.resolve().as_uri()


def test_rejects_root_entry_with_both_path_and_uri(tmp_path: Path) -> None:
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()
    cfg = f"""
mcp:
  roots:
    entries:
      - path: {root_dir}
        uri: {root_dir.resolve().as_uri()}
"""

    _assert_config_error(tmp_path, cfg, "exactly one of uri or path")


def test_rejects_non_file_root_uri(tmp_path: Path) -> None:
    cfg = """
mcp:
  roots:
    entries:
      - uri: https://example.com/root
"""

    _assert_config_error(tmp_path, cfg, "file://")


def test_loads_external_source_auth_settings(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: preregistered
        client_id: client-123
        client_secret_env: MCP_SECRET
        scopes: [tools.read, tools.write]
        redirect_port: 8765
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    source = settings.mcp.sources["remote"]
    assert isinstance(source, MCPExternalSourceSettings)
    assert source.auth is not None
    assert source.auth.mode == "preregistered"
    assert source.auth.client_id == "client-123"
    assert source.auth.scopes == ["tools.read", "tools.write"]
    assert source.auth.redirect_port == 8765


def test_rejects_preregistered_auth_without_client_id(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: preregistered
"""

    _assert_config_error(tmp_path, cfg, "client_id is required")


def test_rejects_client_metadata_mode_without_url(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: client_metadata
"""

    _assert_config_error(tmp_path, cfg, "client_metadata_url is required")


def test_rejects_client_metadata_url_without_https(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: client_metadata
        client_metadata_url: http://example.com/metadata
"""

    _assert_config_error(tmp_path, cfg, "client_metadata_url must use https")


def test_rejects_non_local_redirect_host(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: preregistered
        client_id: client-123
        redirect_host: example.com
"""

    _assert_config_error(tmp_path, cfg, "redirect_host must use localhost")


def test_rejects_dynamic_mode_without_registration_enabled(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: dynamic
"""

    _assert_config_error(tmp_path, cfg, "allow_dynamic_registration")


def test_resolves_variables_in_auth_fields(tmp_path: Path) -> None:
    cfg = """
variables:
  MCP_CLIENT_ID: client-from-var
mcp:
  sources:
    remote:
      kind: external
      url: https://example.com/mcp
      auth:
        mode: preregistered
        client_id: ${MCP_CLIENT_ID}
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    source = settings.mcp.sources["remote"]
    assert isinstance(source, MCPExternalSourceSettings)
    assert source.auth is not None
    assert source.auth.client_id == "client-from-var"


def test_rejects_invalid_external_source_url_scheme(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    remote:
      kind: external
      url: ftp://example.com/mcp
"""

    _assert_config_error(tmp_path, cfg, "http:// or https://")


def test_rejects_invalid_mcp_source_name(tmp_path: Path) -> None:
    cfg = """
mcp:
  sources:
    bad source:
      kind: stdio
      command: uvx
"""

    _assert_config_error(tmp_path, cfg, "mcp source names")


def test_rejects_append_roots_without_entries(tmp_path: Path) -> None:
    cfg = """
mcp:
  roots:
    merge_mode: append
    entries: []
"""

    _assert_config_error(tmp_path, cfg, "append root merge_mode")


def test_loads_mcp_config_from_include(tmp_path: Path) -> None:
    included = tmp_path / "mcp-shared.yaml"
    included.write_text(
        """
mcp:
  protocol:
    request_timeout_s: 12
  sources:
    local:
      kind: stdio
      command: uvx
      args: [shared-server]
""",
        encoding="utf-8",
    )
    cfg = """
$include:
  - local: mcp-shared.yaml
workflows:
  demo:
    nodes: []
    edges: []
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    assert settings.mcp.protocol is not None
    assert settings.mcp.protocol.request_timeout_s == 12
    source = settings.mcp.sources["local"]
    assert isinstance(source, MCPStdioSourceSettings)
    assert source.args == ["shared-server"]


def test_loads_mcp_config_with_include_var_prefix_and_inline_overrides(
    tmp_path: Path,
) -> None:
    included = tmp_path / "mcp-source.yaml"
    included.write_text(
        """
variables:
  HOST: shared.example.com
  CLIENT_ID: shared-client
mcp:
  sources:
    remote:
      kind: external
      url: https://${REMOTE_HOST}/mcp
      auth:
        mode: preregistered
        client_id: ${REMOTE_CLIENT_ID}
""",
        encoding="utf-8",
    )
    cfg = """
$include:
  - local: mcp-source.yaml
    var_prefix: REMOTE_
    vars:
      HOST: overridden.example.com
workflows:
  demo:
    nodes: []
    edges: []
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    source = settings.mcp.sources["remote"]
    assert isinstance(source, MCPExternalSourceSettings)
    assert source.url == "https://overridden.example.com/mcp"
    assert source.auth is not None
    assert source.auth.client_id == "shared-client"


def test_loads_mcp_config_from_multiple_includes_with_override(tmp_path: Path) -> None:
    base = tmp_path / "mcp-base.yaml"
    base.write_text(
        """
mcp:
  protocol:
    request_timeout_s: 10
  sources:
    local:
      kind: stdio
      command: uvx
      args: [base-server]
""",
        encoding="utf-8",
    )
    override = tmp_path / "mcp-override.yaml"
    override.write_text(
        """
mcp:
  sources:
    local:
      args: [override-server]
""",
        encoding="utf-8",
    )
    cfg = """
$include:
  - local: mcp-base.yaml
  - local: mcp-override.yaml
workflows:
  demo:
    nodes: []
    edges: []
"""
    settings = load_settings(str(_write_tmp(tmp_path, cfg)))

    assert settings.mcp is not None
    source = settings.mcp.sources["local"]
    assert isinstance(source, MCPStdioSourceSettings)
    assert source.command == "uvx"
    assert source.args == ["override-server"]
