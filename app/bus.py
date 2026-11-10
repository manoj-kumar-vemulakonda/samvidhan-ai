"""
Live updates for the web page.

Background jobs (reading uploads, making plans, reminders, review tables) call
    bus.publish("upload", {...})
from any thread, and every open browser tab receives it through one Server-Sent Events
stream (/api/stream). The page then refreshes just the part that changed.
"""

from __future__ import annotations

import asyncio
import json
import threading


class Bus:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at start-up with the web server's event loop."""
        self._loop = loop

    def publish(self, kind: str, data: dict | None = None) -> None:
        """Send an event to every open page. Safe to call from any thread."""
        message = {"kind": kind, **(data or {})}
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
            except RuntimeError:  # the loop has stopped
                pass

    async def listen(self):
        """Async generator of events for one page (ends when the page goes away)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._queues.add(queue)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # stops proxies and browsers from closing the stream
                    continue
                yield f"event: {message['kind']}\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
        finally:
            with self._lock:
                self._queues.discard(queue)


bus = Bus()
