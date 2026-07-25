from __future__ import annotations

import asyncio
import unittest

from backend.v2.api.events import _encode_sse, _event_stream, create_event_router
from backend.v2.container import V2Runtime
from backend.v2.events import V2Event, V2EventBroker


class EventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_from_thread_reaches_subscriber(self):
        broker = V2EventBroker(queue_size=2)

        async with broker.subscribe() as queue:
            await asyncio.to_thread(
                broker.publish,
                "episode.updated",
                {"episodeId": "e1", "status": "processing", "progress": 20},
            )
            event = await asyncio.wait_for(queue.get(), 1)

        self.assertEqual(event.type, "episode.updated")
        self.assertEqual(event.data["episodeId"], "e1")
        self.assertEqual(event.id, "1")

    async def test_slow_subscriber_keeps_newest_event(self):
        broker = V2EventBroker(queue_size=1)

        async with broker.subscribe() as queue:
            broker.publish("episode.updated", {"episodeId": "old"})
            broker.publish("episode.updated", {"episodeId": "new"})
            event = await asyncio.wait_for(queue.get(), 1)

        self.assertEqual(event.data["episodeId"], "new")
        self.assertEqual(event.id, "2")


class EventRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_starts_with_retry_and_sends_idle_heartbeat(self):
        stream = _event_stream(V2EventBroker(), heartbeat_seconds=0.001)
        try:
            self.assertEqual(await anext(stream), "retry: 3000\n\n")
            self.assertEqual(
                await anext(stream),
                "event: heartbeat\ndata: {}\n\n",
            )
        finally:
            await stream.aclose()

    def test_event_encoding_uses_named_compact_json_frame(self):
        event = V2Event("7", "episode.updated", {"episodeId": "e1"})

        self.assertEqual(
            _encode_sse(event),
            'id: 7\nevent: episode.updated\ndata: {"episodeId":"e1"}\n\n',
        )

    async def test_route_returns_non_buffered_event_stream(self):
        runtime = V2Runtime()
        router = create_event_router(runtime)
        route = next(
            route
            for route in router.routes
            if getattr(route, "path", None) == "/events"
        )

        response = await route.endpoint()

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")


if __name__ == "__main__":
    unittest.main()
