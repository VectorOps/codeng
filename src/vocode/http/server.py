from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Dict, Tuple

from aiohttp import web
from aiohttp.web_urldispatcher import UrlDispatcher

from vocode.settings import InternalHTTPSettings


RouteHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass
class RouteHandle:
    method: str
    path: str


class InternalHTTPConfigError(Exception):
    pass


class InternalHTTPRouteError(Exception):
    pass


class InternalHTTPServer:
    def __init__(self, config: InternalHTTPSettings) -> None:
        self._config = config
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._running: bool = False

        # Your registry (still the “source of truth”)
        self._routes: Dict[Tuple[str, str], RouteHandler] = {}

        # NEW: aiohttp’s matcher
        self._dispatcher: UrlDispatcher = UrlDispatcher()

        self._usage_count: int = 0
        self._lock = asyncio.Lock()

    @property
    def config(self) -> InternalHTTPSettings:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    async def _ensure_started(self) -> None:
        if self._running:
            return
        if self._config.port is None:
            raise InternalHTTPConfigError("internal HTTP server port is not configured")

        app = web.Application()
        # keep the catch-all
        app.router.add_route("*", "/{tail:.*}", self._dispatch)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self._config.host, port=self._config.port)
        await site.start()

        self._app = app
        self._runner = runner
        self._site = site
        self._running = True

    async def _shutdown_if_idle(self) -> None:
        if self._usage_count != 0 or not self._running:
            return
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        self._app = None
        self._runner = None
        self._site = None
        self._running = False

    def _rebuild_dispatcher(self) -> None:
        # UrlDispatcher doesn’t have a clean “remove route” API.
        # Rebuild it from the active registry so deregister + re-register works correctly.
        d = UrlDispatcher()
        for (method, path), handler in self._routes.items():
            d.add_route(method, path, handler)
        self._dispatcher = d

    async def register_route(self, method: str, path: str, handler: RouteHandler) -> RouteHandle:
        if self._config.port is None:
            raise InternalHTTPConfigError("internal HTTP server port is not configured")
        norm_method = method.upper()
        norm_path = path
        key = (norm_method, norm_path)

        async with self._lock:
            if key in self._routes:
                raise InternalHTTPRouteError(f"route already registered: {norm_method} {norm_path}")

            await self._ensure_started()

            self._routes[key] = handler
            self._rebuild_dispatcher()

            self._usage_count += 1

        return RouteHandle(method=norm_method, path=norm_path)

    async def deregister_route(self, handle: RouteHandle) -> None:
        norm_method = handle.method.upper()
        norm_path = handle.path
        key = (norm_method, norm_path)

        async with self._lock:
            if key not in self._routes:
                raise InternalHTTPRouteError(f"route not registered: {norm_method} {norm_path}")

            del self._routes[key]
            self._rebuild_dispatcher()

            if self._usage_count > 0:
                self._usage_count -= 1

            await self._shutdown_if_idle()

    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        # Let aiohttp match registered routes (including params)
        match_info = await self._dispatcher.resolve(request)

        # Important: make params available to downstream code as request.match_info
        # (aiohttp normally sets this before calling the handler)
        request._match_info = match_info  # type: ignore[attr-defined]

        # If no match, aiohttp will typically return a handler that raises HTTPNotFound.
        try:
            return await match_info.handler(request)
        except web.HTTPNotFound:
            return web.Response(status=404, text="Not Found")


# Singleton and helpers        
_config: InternalHTTPSettings = InternalHTTPSettings()
_server: Optional[InternalHTTPServer] = None


def configure_internal_http(config: InternalHTTPSettings) -> None:
    global _server
    global _config
    _config = config
    _server = InternalHTTPServer(config=config)


def get_internal_http_server() -> InternalHTTPServer:
    global _server
    if _server is None:
        _server = InternalHTTPServer(config=_config)
    return _server


async def add_route(method: str, path: str, handler: RouteHandler) -> RouteHandle:
    server = get_internal_http_server()
    return await server.register_route(method, path, handler)


async def remove_route(handle: RouteHandle) -> None:
    server = get_internal_http_server()
    await server.deregister_route(handle)


def is_running() -> bool:
    return get_internal_http_server().is_running


def require_internal_auth(handler: RouteHandler) -> RouteHandler:
    async def wrapper(request: web.Request) -> web.StreamResponse:
        server = get_internal_http_server()
        secret = server.config.secret_key
        if not secret:
            return await handler(request)
        auth_header = request.headers.get("Authorization")
        expected = f"Bearer {secret}"
        if auth_header != expected:
            return web.Response(status=401, text="Unauthorized")
        return await handler(request)

    return wrapper
