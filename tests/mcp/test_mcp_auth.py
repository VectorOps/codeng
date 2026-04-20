from __future__ import annotations

import hashlib

import pytest
from aiohttp import web

from vocode.connect_auth import ProjectCredentialManager
from vocode.mcp.auth import MCPAuthManager
from vocode import settings as vocode_settings


def _token_key(source_name: str, resource_uri: str) -> str:
    digest = hashlib.sha256(resource_uri.encode("utf-8")).hexdigest()
    normalized = source_name.replace("-", "_").replace(".", "_")
    return f"MCP_TOKEN_{normalized.upper()}_{digest[:16].upper()}"


def test_parse_www_authenticate_header_extracts_bearer_params() -> None:
    manager = MCPAuthManager(vocode_settings.MCPSettings())

    challenge = manager.parse_www_authenticate(
        'Bearer realm="mcp", resource="https://example.com/mcp", scope="tools.read"'
    )

    assert challenge is not None
    assert challenge.scheme == "Bearer"
    assert challenge.params == {
        "realm": "mcp",
        "resource": "https://example.com/mcp",
        "scope": "tools.read",
    }


@pytest.mark.asyncio
async def test_auth_manager_discovers_and_caches_preregistered_token(
    tmp_path,
    unused_tcp_port,
) -> None:
    port = unused_tcp_port
    base_url = f"http://127.0.0.1:{port}"
    resource_url = f"{base_url}/mcp"
    observed = {"token_requests": 0}

    async def protected_resource_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "resource": resource_url,
                "authorization_servers": [f"{base_url}/issuer"],
                "scopes_supported": ["tools.read"],
            }
        )

    async def authorization_server_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "issuer": f"{base_url}/issuer",
                "token_endpoint": f"{base_url}/issuer/token",
                "code_challenge_methods_supported": ["S256"],
            }
        )

    async def token_handler(request: web.Request) -> web.Response:
        observed["token_requests"] += 1
        data = await request.post()
        assert data["client_id"] == "client-123"
        assert data["client_secret"] == "secret"
        assert data["resource"] == resource_url
        assert data["scope"] == "tools.read"
        return web.json_response(
            {
                "access_token": "fresh-token",
                "token_type": "Bearer",
                "expires_in": 600,
                "scope": "tools.read",
            }
        )

    app = web.Application()
    app.router.add_get(
        "/.well-known/oauth-protected-resource/mcp",
        protected_resource_handler,
    )
    app.router.add_get(
        "/issuer/.well-known/oauth-authorization-server",
        authorization_server_handler,
    )
    app.router.add_post("/issuer/token", token_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    credentials = ProjectCredentialManager(
        env={"MCP_SECRET": "secret"},
        credentials_path=tmp_path / "credentials.json",
    )
    manager = MCPAuthManager(
        vocode_settings.MCPSettings(),
        credentials=credentials,
    )
    source = vocode_settings.MCPExternalSourceSettings(
        url=resource_url,
        auth=vocode_settings.MCPAuthSettings(
            mode="preregistered",
            client_id="client-123",
            client_secret_env="MCP_SECRET",
            scopes=["tools.read"],
        ),
    )

    headers1 = await manager.resolve_headers("remote", source)
    headers2 = await manager.resolve_headers("remote", source)

    assert headers1["Authorization"] == "Bearer fresh-token"
    assert headers2["Authorization"] == "Bearer fresh-token"
    assert observed["token_requests"] == 1
    cached = await credentials.get_token(_token_key("remote", resource_url))
    assert cached is not None

    await runner.cleanup()
