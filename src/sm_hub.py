# sm_hub.py — ELIA Stage 0
# Central message bus: routes messages between modules.
# All inter-module communication passes through here.

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List
import uuid


@dataclass
class Message:
    """Standard message format for all inter-module communication."""

    source: str
    destination: str
    topic: str
    payload: dict
    priority: str = "normal"  # low | normal | high | critical
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SMHub:
    """
    SM_HUB — Message Bus (Stage 0 MVP)

    Responsibilities:
    - Route messages between modules.
    - Support topic-based subscriptions.
    - Detect delivery failures and notify SM_GSM (future stage).

    MVP scope: in-process async queue, no broker, no persistence.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        print("[SM_HUB] Initialized.")

    def subscribe(self, topic: str, handler: Callable):
        """Register a handler function for a given topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)
        print(f"[SM_HUB] Module subscribed to topic: '{topic}'")

    async def publish(self, message: Message):
        """Publish a message to the queue for routing."""
        await self._queue.put(message)
        print(
            f"[SM_HUB] Message queued | {message.source} → {message.destination} | topic: {message.topic}"
        )

    async def _route(self, message: Message):
        """Internal: deliver message to all subscribers of its topic."""
        handlers = self._subscribers.get(message.topic, [])
        if not handlers:
            print(f"[SM_HUB] Warning: no subscriber for topic '{message.topic}'")
            return
        for handler in handlers:
            try:
                await handler(message)
            except Exception as e:
                print(f"[SM_HUB] Delivery error on topic '{message.topic}': {e}")

    async def run(self):
        """Main routing loop. Runs until stopped."""
        self._running = True
        print("[SM_HUB] Routing loop started.")
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._route(message)
            except asyncio.TimeoutError:
                pass  # No messages, keep waiting

    def stop(self):
        """Stop the routing loop."""
        self._running = False
        print("[SM_HUB] Stopped.")
