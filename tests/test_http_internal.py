from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from vocode.http import server as http_server
from vocode.settings import InternalHTTPSettings


@pytest.mark.asyncio
async def test_disabled_by_default_raises_on_add_route() -> None:
    http_server.configure_internal_http(InternalHTTPSettings())

    async def handler(request):
        return None

    with pytest.raises(http_server.InternalHTTPConfigError):
        await http_server.add_route("GET", "/ping", handler)


@pytest.mark.asyncio
async def test_basic_request_handling(tmp_path) -> None:
    settings = InternalHTTPSettings(host="127.0.0.1", port=0)
    http_server.configure_internal_http(settings)

    async def handler(request):
        return web.Response(text="ok")

    handle = await http_server.add_route("GET", "/ping", handler)
    assert http_server.is_running() is True

    srv = http_server.get_internal_http_server()
    runner = srv._runner
    assert runner is not None
    sites = list(runner.sites)
    assert sites
    site = sites[0]
    sockets = list(site._server.sockets) if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]

    async with ClientSession() as session:
        async with session.get(f"http://{host}:{port}/ping") as resp:
            text = await resp.text()
            assert resp.status == 200
            assert text == "ok"

    await http_server.remove_route(handle)


@pytest.mark.asyncio
async def test_variable_route_support(tmp_path) -> None:
    settings = InternalHTTPSettings(host="127.0.0.1", port=0)
    http_server.configure_internal_http(settings)

    async def handler(request):
        item_id = request.match_info.get("item_id")
        return web.Response(text=str(item_id))

    handle = await http_server.add_route("GET", "/items/{item_id}", handler)
    assert http_server.is_running() is True

    srv = http_server.get_internal_http_server()
    runner = srv._runner
    assert runner is not None
    sites = list(runner.sites)
    assert sites
    site = sites[0]
    sockets = list(site._server.sockets) if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]

    async with ClientSession() as session:
        async with session.get(f"http://{host}:{port}/items/123") as resp:
            text = await resp.text()
            assert resp.status == 200
            assert text == "123"

    await http_server.remove_route(handle)
    await asyncio.sleep(0.05)
    assert http_server.is_running() is False


@pytest.mark.asyncio
async def test_auth_decorator_enforces_secret(tmp_path) -> None:
    settings = InternalHTTPSettings(host="127.0.0.1", port=0, secret_key="secret")
    http_server.configure_internal_http(settings)

    async def handler(request):
        return web.Response(text="ok")

    protected = http_server.require_internal_auth(handler)
    handle = await http_server.add_route("GET", "/secure", protected)

    srv = http_server.get_internal_http_server()
    runner = srv._runner
    assert runner is not None
    sites = list(runner.sites)
    assert sites
    site = sites[0]
    sockets = list(site._server.sockets) if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]

    async with ClientSession() as session:
        async with session.get(f"http://{host}:{port}/secure") as resp:
            assert resp.status == 401
        async with session.get(
            f"http://{host}:{port}/secure",
            headers={"Authorization": "Bearer secret"},
        ) as resp:
            text = await resp.text()
            assert resp.status == 200
            assert text == "ok"

    await http_server.remove_route(handle)
