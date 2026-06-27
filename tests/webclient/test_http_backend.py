from __future__ import annotations

import asyncio
import base64

import pytest
from aiohttp import web

from vocode.webclient import errors as webclient_errors
from vocode.webclient import models as webclient_models
from vocode.webclient.backends.http import HTTPWebClientBackend
from vocode.webclient.pipeline import process_raw_content


async def _start_test_server(unused_tcp_port, handler_map: dict[str, object]):
    app = web.Application()
    for path, handler in handler_map.items():
        app.router.add_route("*", path, handler)
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
async def test_http_backend_sends_post_text_body(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/json"
        body = await request.text()
        return web.json_response({"received": body})

    runner, base_url = await _start_test_server(unused_tcp_port, {"/post": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(
            url=f"{base_url}/post",
            method=webclient_models.WebClientMethod.post,
            body=webclient_models.WebClientRequestBodyText(
                content_type="application/json",
                text='{"name":"test"}',
            ),
        )
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
        )
        result = await backend.fetch(request, settings)
    finally:
        await runner.cleanup()

    assert result.status_code == 200
    assert result.bytes_body == b'{"received": "{\\"name\\":\\"test\\"}"}'


@pytest.mark.asyncio
async def test_http_backend_sends_put_binary_body(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert request.method == "PUT"
        assert request.headers["Content-Type"] == "application/octet-stream"
        body = await request.read()
        return web.Response(body=body, content_type="application/octet-stream")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/put": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(
            url=f"{base_url}/put",
            method=webclient_models.WebClientMethod.put,
            body=webclient_models.WebClientRequestBodyBinary(
                content_type="application/octet-stream",
                data_base64=base64.b64encode(b"\x00\x01\x02").decode("ascii"),
            ),
        )
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
        )
        result = await backend.fetch(request, settings)
    finally:
        await runner.cleanup()

    assert result.status_code == 200
    assert result.bytes_body == b"\x00\x01\x02"


def test_web_client_request_rejects_body_for_get() -> None:
    with pytest.raises(ValueError, match="request body is not allowed"):
        webclient_models.WebClientRequest(
            url="https://example.com",
            method=webclient_models.WebClientMethod.get,
            body=webclient_models.WebClientRequestBodyText(
                content_type="text/plain",
                text="hello",
            ),
        )


@pytest.mark.asyncio
async def test_http_backend_rejects_body_over_limit(unused_tcp_port) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/post": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(
            url=f"{base_url}/post",
            method=webclient_models.WebClientMethod.post,
            body=webclient_models.WebClientRequestBodyText(
                content_type="text/plain",
                text="0123456789",
            ),
        )
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
            max_request_body_bytes=4,
        )
        with pytest.raises(
            webclient_errors.WebClientValidationError,
            match="request body exceeds max_request_body_bytes",
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_backend_rejects_disallowed_request_content_type(
    unused_tcp_port,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/post": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(
            url=f"{base_url}/post",
            method=webclient_models.WebClientMethod.post,
            body=webclient_models.WebClientRequestBodyText(
                content_type="application/json",
                text='{"name":"test"}',
            ),
        )
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
            allowed_request_content_types=["text/plain"],
        )
        with pytest.raises(
            webclient_errors.WebClientValidationError,
            match="request body content type is not allowed",
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_backend_rejects_conflicting_content_type_header(
    unused_tcp_port,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    runner, base_url = await _start_test_server(unused_tcp_port, {"/post": handler})
    try:
        backend = HTTPWebClientBackend()
        request = webclient_models.WebClientRequest(
            url=f"{base_url}/post",
            method=webclient_models.WebClientMethod.post,
            headers={"Content-Type": "text/plain"},
            body=webclient_models.WebClientRequestBodyText(
                content_type="application/json",
                text='{"name":"test"}',
            ),
        )
        settings = webclient_models.WebClientSettings(
            timeout_s=2.0,
            connect_timeout_s=1.0,
            read_timeout_s=2.0,
        )
        with pytest.raises(
            webclient_errors.WebClientValidationError,
            match="Content-Type header must match request body content_type",
        ):
            await backend.fetch(request, settings)
    finally:
        await runner.cleanup()


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


def test_process_raw_content_limits_text_bytes() -> None:
    raw = webclient_models.WebClientRawContent(
        url="https://example.com/test.txt",
        final_url="https://example.com/test.txt",
        status_code=200,
        content_type="text/plain",
        text="abcdefghijklmno",
    )

    result = process_raw_content(
        raw,
        webclient_models.WebClientSettings(max_text_bytes=10),
    )

    assert result.text == "abcdef\n..."
