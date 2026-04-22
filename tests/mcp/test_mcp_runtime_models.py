import pytest

from vocode.mcp.models import MCPClientCapabilities
from vocode.mcp.models import MCPPromptDescriptor
from vocode.mcp.models import MCPResourceDescriptor
from vocode.mcp.models import MCPServerCapabilities
from vocode.mcp.models import MCPSessionNegotiation
from vocode.mcp.models import MCPSessionPhase
from vocode.mcp.models import MCPSessionState
from vocode.mcp.models import MCPSourceDescriptor
from vocode.mcp.models import MCPTransportKind


def test_builds_valid_mcp_session_state() -> None:
    source = MCPSourceDescriptor(
        source_name="local",
        transport=MCPTransportKind.stdio,
        scope="workflow",
        startup_timeout_s=15,
        shutdown_timeout_s=10,
        request_timeout_s=30,
        max_request_timeout_s=60,
    )
    negotiation = MCPSessionNegotiation(protocol_version="2025-03-26")

    session = MCPSessionState(
        source=source,
        phase=MCPSessionPhase.operating,
        negotiation=negotiation,
        initialized=True,
    )

    assert session.source.source_name == "local"
    assert session.phase == MCPSessionPhase.operating
    assert session.negotiation.protocol_version == "2025-03-26"
    assert session.initialized is True


def test_rejects_invalid_source_timeout_bounds() -> None:
    with pytest.raises(ValueError, match="max_request_timeout_s"):
        MCPSourceDescriptor(
            source_name="local",
            transport=MCPTransportKind.stdio,
            scope="workflow",
            startup_timeout_s=15,
            shutdown_timeout_s=10,
            request_timeout_s=30,
            max_request_timeout_s=20,
        )


def test_rejects_roots_list_changed_without_roots_client_capability() -> None:
    with pytest.raises(ValueError, match="roots_list_changed requires roots"):
        MCPClientCapabilities(roots=False, roots_list_changed=True)


def test_client_capabilities_initialize_payload_omits_unimplemented_capabilities() -> (
    None
):
    assert MCPClientCapabilities().to_initialize_payload() == {}
    assert MCPClientCapabilities(roots=True).to_initialize_payload() == {"roots": {}}
    assert MCPClientCapabilities(
        roots=True,
        roots_list_changed=True,
    ).to_initialize_payload() == {"roots": {"listChanged": True}}


def test_rejects_tools_list_changed_without_tools_server_capability() -> None:
    with pytest.raises(ValueError, match="tools_list_changed requires tools"):
        MCPServerCapabilities(tools=False, tools_list_changed=True)


def test_rejects_operating_session_without_initialization() -> None:
    source = MCPSourceDescriptor(
        source_name="remote",
        transport=MCPTransportKind.http,
        scope="project",
        startup_timeout_s=15,
        shutdown_timeout_s=10,
        request_timeout_s=30,
    )

    with pytest.raises(ValueError, match="operating session must be initialized"):
        MCPSessionState(
            source=source,
            phase=MCPSessionPhase.operating,
            initialized=False,
        )


def test_rejects_initialized_session_in_initializing_phase() -> None:
    source = MCPSourceDescriptor(
        source_name="remote",
        transport=MCPTransportKind.http,
        scope="project",
        startup_timeout_s=15,
        shutdown_timeout_s=10,
        request_timeout_s=30,
    )

    with pytest.raises(ValueError, match="initialized session may not remain"):
        MCPSessionState(
            source=source,
            phase=MCPSessionPhase.initializing,
            initialized=True,
        )


def test_prompt_descriptor_requires_non_empty_name() -> None:
    with pytest.raises(ValueError, match="prompt_name"):
        MCPPromptDescriptor(
            source_name="local",
            prompt_name="",
        )


def test_resource_descriptor_requires_non_empty_uri() -> None:
    with pytest.raises(ValueError, match="uri"):
        MCPResourceDescriptor(
            source_name="local",
            uri="",
        )
