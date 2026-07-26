from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True)
class V2Event:
    id: str
    type: str
    data: dict[str, object]


EventPublisher = Callable[[str, dict[str, object]], object]


def ignore_event(_event_type: str, _data: dict[str, object]) -> None:
    return None


class V2EventBroker:
    def __init__(self, queue_size: int = 64):
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._next_id = 1
        self._subscribers: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Queue[V2Event]]
        ] = {}

    def publish(
        self, event_type: str, data: dict[str, object] | None = None
    ) -> V2Event:
        with self._lock:
            event = V2Event(str(self._next_id), event_type, dict(data or {}))
            self._next_id += 1
            subscribers = tuple(self._subscribers.values())
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_offer_latest, queue, event)
            except RuntimeError:
                # The subscription context removes closed loops. A concurrent
                # loop shutdown may race with this best-effort invalidation.
                continue
        return event

    @asynccontextmanager
    async def subscribe(self):
        token = uuid4().hex
        queue: asyncio.Queue[V2Event] = asyncio.Queue(maxsize=self._queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[token] = (loop, queue)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.pop(token, None)


def _offer_latest(queue: asyncio.Queue[V2Event], event: V2Event) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass
