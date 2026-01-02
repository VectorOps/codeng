from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, Optional

from vocode.logger import logger

from .proto import BasePacket, BasePacketEnvelope, BasePacketKind


class BaseEndpoint:
    async def send(self, envelope: BasePacketEnvelope) -> None:
        pass

    async def recv(self) -> BasePacketEnvelope:
        pass


class InMemoryEndpoint(BaseEndpoint):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[BasePacketEnvelope] = asyncio.Queue()
        self._peer: Optional["InMemoryEndpoint"] = None

    @classmethod
    def pair(cls) -> tuple["InMemoryEndpoint", "InMemoryEndpoint"]:
        a = cls()
        b = cls()
        a._peer = b
        b._peer = a
        return a, b

    async def send(self, envelope: BasePacketEnvelope) -> None:
        if self._peer is None:
            raise RuntimeError("Endpoint has no peer")
        await self._peer._incoming.put(envelope)

    async def recv(self) -> BasePacketEnvelope:
        envelope = await self._incoming.get()
        return envelope


class RpcHelper:
    def __init__(
        self,
        send_callback: Callable[[BasePacketEnvelope], Awaitable[None]],
        name: str,
        id_generator: Optional[Callable[[], int]] = None,
    ) -> None:
        self._send_callback = send_callback
        self._name = name
        self._id_generator = id_generator
        self._pending_requests: Dict[int, "asyncio.Future[BasePacketEnvelope]"] = {}
        self._msg_id_counter = 0

    def _next_msg_id(self) -> int:
        if self._id_generator is not None:
            return self._id_generator()
        self._msg_id_counter += 1
        return self._msg_id_counter

    async def call(
        self,
        payload: BasePacket,
        timeout: Optional[float] = 300.0,
    ) -> Optional[BasePacket]:
        msg_id = self._next_msg_id()
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[BasePacketEnvelope]" = loop.create_future()
        self._pending_requests[msg_id] = fut

        envelope = BasePacketEnvelope(msg_id=msg_id, payload=payload)
        await self._send_callback(envelope)

        try:
            response_envelope = await asyncio.wait_for(fut, timeout=timeout)
            if response_envelope.payload.kind == BasePacketKind.ACK:
                return None
            return response_envelope.payload
        except asyncio.TimeoutError:
            logger.error("%s: request %d timed out", self._name, msg_id)
            raise
        finally:
            self._pending_requests.pop(msg_id, None)

    async def reply(self, payload: BasePacket, source_msg_id: int) -> None:
        msg_id = self._next_msg_id()
        envelope = BasePacketEnvelope(
            msg_id=msg_id,
            payload=payload,
            source_msg_id=source_msg_id,
        )
        await self._send_callback(envelope)

    def handle_response(self, envelope: BasePacketEnvelope) -> bool:
        if envelope.source_msg_id is None:
            return False
        fut = self._pending_requests.get(envelope.source_msg_id)
        if fut is not None and not fut.done():
            fut.set_result(envelope)
            return True
        return False

    def cancel_all(self) -> None:
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel(f"{self._name} RPC client is shutting down")
        self._pending_requests.clear()


class IncomingPacketRouter:
    def __init__(self, rpc: RpcHelper, name: str) -> None:
        self._rpc = rpc
        self._name = name
        self._handlers: Dict[
            BasePacketKind,
            Callable[[BasePacketEnvelope], Awaitable[Optional[BasePacket]]],
        ] = {}

    def register(
        self,
        kind: BasePacketKind,
        handler: Callable[[BasePacketEnvelope], Awaitable[Optional[BasePacket]]],
    ) -> None:
        self._handlers[kind] = handler

    async def handle(self, envelope: BasePacketEnvelope) -> bool:
        if envelope.source_msg_id is not None:
            matched = self._rpc.handle_response(envelope)
            if not matched:
                logger.debug(
                    "%s: unmatched response source_msg_id=%s kind=%s",
                    self._name,
                    envelope.source_msg_id,
                    envelope.payload.kind,
                )
            return True

        kind = envelope.payload.kind
        handler = self._handlers.get(kind)
        if handler is None:
            logger.error("%s: no handler for request kind=%s", self._name, kind)
            return False

        resp = await handler(envelope)
        if resp is not None:
            await self._rpc.reply(resp, source_msg_id=envelope.msg_id)
        return True
