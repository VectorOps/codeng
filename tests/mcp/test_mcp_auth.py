from __future__ import annotations

import hashlib
from urllib import parse

import pytest
from aiohttp import web

from vocode.auth import ProjectCredentialManager
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


def test_build_authorization_request_url_includes_resource_parameter() -> None:
    manager = MCPAuthManager(vocode_settings.MCPSettings())

    url = manager.build_authorization_request_url(
        "https://issuer.example/authorize?audience=existing",
        client_id="client-123",
        redirect_uri="http://127.0.0.1:8123/callback",
        state="state-123",
        code_challenge="challenge-123",
        resource_uri="https://example.com/mcp?ignored=yes",
        scopes=["tools.read", "tools.write"],
    )

    parsed = parse.urlsplit(url)
    params = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))

    assert params["audience"] == "existing"
    assert params["response_type"] == "code"
    assert params["client_id"] == "client-123"
    assert params["redirect_uri"] == "http://127.0.0.1:8123/callback"
    assert params["state"] == "state-123"
    assert params["code_challenge"] == "challenge-123"
    assert params["code_challenge_method"] == "S256"
    assert params["resource"] == "https://example.com/mcp"
    assert params["scope"] == "tools.read tools.write"


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


@pytest.mark.asyncio
async def test_auth_manager_reuses_persisted_token_across_restarts(
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
            }
        )

    async def authorization_server_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "issuer": f"{base_url}/issuer",
                "token_endpoint": f"{base_url}/issuer/token",
            }
        )

    async def token_handler(request: web.Request) -> web.Response:
        observed["token_requests"] += 1
        data = await request.post()
        assert data["resource"] == resource_url
        return web.json_response(
            {
                "access_token": "persisted-token",
                "token_type": "Bearer",
                "expires_in": 600,
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

    credentials_path = tmp_path / "credentials.json"
    source = vocode_settings.MCPExternalSourceSettings(
        url=resource_url,
        auth=vocode_settings.MCPAuthSettings(
            mode="preregistered",
            client_id="client-123",
            client_secret_env="MCP_SECRET",
        ),
    )

    credentials1 = ProjectCredentialManager(
        env={"MCP_SECRET": "secret"},
        credentials_path=credentials_path,
    )
    manager1 = MCPAuthManager(
        vocode_settings.MCPSettings(),
        credentials=credentials1,
    )

    headers1 = await manager1.resolve_headers("remote", source)

    credentials2 = ProjectCredentialManager(
        env={"MCP_SECRET": "secret"},
        credentials_path=credentials_path,
    )
    manager2 = MCPAuthManager(
        vocode_settings.MCPSettings(),
        credentials=credentials2,
    )

    headers2 = await manager2.resolve_headers("remote", source)

    assert headers1["Authorization"] == "Bearer persisted-token"
    assert headers2["Authorization"] == "Bearer persisted-token"
    assert observed["token_requests"] == 1

    await runner.cleanup()


@pytest.mark.asyncio
async def test_auth_manager_step_up_retries_with_scope_from_403_challenge(
    tmp_path,
    unused_tcp_port,
) -> None:
    port = unused_tcp_port
    base_url = f"http://127.0.0.1:{port}"
    resource_url = f"{base_url}/mcp"
    observed = {"scopes": []}

    async def protected_resource_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "resource": resource_url,
                "authorization_servers": [f"{base_url}/issuer"],
            }
        )

    async def authorization_server_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "issuer": f"{base_url}/issuer",
                "token_endpoint": f"{base_url}/issuer/token",
            }
        )

    async def token_handler(request: web.Request) -> web.Response:
        data = await request.post()
        observed["scopes"].append(data.get("scope"))
        return web.json_response(
            {
                "access_token": f"token-{len(observed['scopes'])}",
                "token_type": "Bearer",
                "expires_in": 600,
                "scope": data.get("scope"),
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
            max_step_up_attempts=2,
        ),
    )

    initial_headers = await manager.resolve_headers("remote", source)
    step_up_headers = await manager.resolve_headers_for_challenge(
        "remote",
        source,
        status_code=403,
        www_authenticate='Bearer error="insufficient_scope", scope="tools.write"',
        step_up_attempt=0,
    )
    blocked_headers = await manager.resolve_headers_for_challenge(
        "remote",
        source,
        status_code=403,
        www_authenticate='Bearer error="insufficient_scope", scope="tools.admin"',
        step_up_attempt=2,
    )

    assert initial_headers["Authorization"] == "Bearer token-1"
    assert step_up_headers == {"Authorization": "Bearer token-2"}
    assert blocked_headers is None
    assert observed["scopes"] == ["tools.read", "tools.read tools.write"]

    await runner.cleanup()
