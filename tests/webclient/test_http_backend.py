from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from vocode.webclient import errors as webclient_errors
from vocode.webclient import models as webclient_models
from vocode.webclient.backends.http import HTTPWebClientBackend


async def _start_test_server(unused_tcp_port, handler_map: dict[str, object]):
    app = web.Application()
    for path, handler in handler_map.items():
        app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    return runner, f"http://127.0.0.1:{unused_tcp_port}"


@pytest.mark.asyncio
async def test_http_backend_fetches_text_response(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="hello", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/text": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(url=f"{base_url}/text")
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
        )
        result = await backend.fetch(request, settings)
    finally:
        await runner.cleanup()

    assert result.status_code == 200
    assert result.content_type == "text/plain; charset=utf-8"
    assert result.bytes_body == b"hello"


@pytest.mark.asyncio
async def test_http_backend_fetches_html_response(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text="<h1>Title</h1><p>hello</p>",
            content_type="text/html",
        )

    runner, base_url = await _start_test_server(unused_tcp_port, {"/html": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(url=f"{base_url}/html")
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
        )
        result = await backend.fetch(request, settings)
    finally:
        await runner.cleanup()

    assert result.status_code == 200
    assert result.content_type == "text/html; charset=utf-8"
    assert b"<h1>Title</h1>" in (result.bytes_body or b"")


@pytest.mark.asyncio
async def test_http_backend_enforces_blocklist(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/text": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(url=f"{base_url}/text")
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
            url_blocklist=["127.0.0.1"],
        )
        with pytest.raises(
            webclient_errors.WebClientAccessError, match="blocked destination"
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_backend_rejects_unsupported_scheme() -> None:
    backend = HTTPWebClientBackend()
    request = webclient_models.WebClientRequest(url="https://example.com")
    settings = webclient_models.WebClientSettings(
        allowed_schemes=["http"],
        timeout_s=2.0,
        connect_timeout_s=1.0,
        read_timeout_s=2.0,
    )
    with pytest.raises(
        webclient_errors.WebClientValidationError, match="unsupported URL scheme"
    ):
        await backend.fetch(request, settings)


@pytest.mark.asyncio
async def test_http_backend_enforces_content_length_limit(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="x" * 32, content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/big": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(url=f"{base_url}/big")
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
            max_content_bytes=8,
        )
        with pytest.raises(
            webclient_errors.WebClientContentError,
            match="response body exceeds max_content_bytes",
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_backend_enforces_timeout(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(0.2)
        return web.Response(text="slow", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/slow": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(url=f"{base_url}/slow")
        settings = webclient_models.WebClientSettings(
            timeout_s=0.05,
            connect_timeout_s=0.05,
            read_timeout_s=0.05,
        )
        with pytest.raises(
            webclient_errors.WebClientFetchError, match="request timed out"
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()
