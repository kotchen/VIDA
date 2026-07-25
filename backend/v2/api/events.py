from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..container import V2Runtime
from ..events import V2Event, V2EventBroker


HEARTBEAT_SECONDS = 15.0


def create_event_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(tags=["events"])

    @router.get("/events")
    async def events():
        return StreamingResponse(
            _event_stream(runtime.events),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def _event_stream(
    broker: V2EventBroker, heartbeat_seconds: float = HEARTBEAT_SECONDS
):
    yield "retry: 3000\n\n"
    async with broker.subscribe() as queue:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=heartbeat_seconds
                )
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
            else:
                yield _encode_sse(event)


def _encode_sse(event: V2Event) -> str:
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"
