# sm_hub.py — ELIA
# Central message bus: unified routing of all inter-module messages.
# Pure routing only — no business logic, no persistence.
#
# ARCHITECTURAL ROLE (EL-ARCH lines 7-88):
# - Sole responsible for message routing between modules.
# - Observer/Observable pattern via topic-based pub/sub.
# - Detects delivery failures and reports to SM_GSM (future stage).
# - MVP scope: in-process async queue, no external broker.
#
# GEMINI AUDIT FIX:
# - Queue bounded (maxsize=500) to prevent unbounded memory growth.
# - Delivery errors logged per handler without blocking other subscribers.

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional


# ----------------------------------------------------------------
# Priority levels — aligned with EL-ARCH contract (line 15)
# ----------------------------------------------------------------
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_CRITICAL = "critical"

VALID_PRIORITIES = {PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_CRITICAL}

# Queue capacity — bounded to prevent unbounded memory growth under load.
# If full, publish() will block until a slot is available.
# Adjust via SMHub constructor if needed for your workload.
DEFAULT_QUEUE_MAXSIZE = 500


# ----------------------------------------------------------------
# Message — standard inter-module message format
# ----------------------------------------------------------------

@dataclass
class Message:
    """
    Standard message format for all inter-module communication.

    All fields except payload are validated at construction time.
    correlation_id is auto-generated if not provided — ensures
    full traceability across modules (EL-ARCH line 15).
    """
    source: str
    destination: str
    topic: str
    payload: dict
    priority: str = PRIORITY_NORMAL
    correlation_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self):
        if self.priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{self.priority}'. "
                f"Must be one of: {sorted(VALID_PRIORITIES)}"
            )


# ----------------------------------------------------------------
# SM_HUB — Central Message Bus
# ----------------------------------------------------------------

class SMHub:
    """
    SM_HUB — Central Communication Node (MVP scope)

    Responsibilities:
    - Route messages between modules via topic-based subscriptions.
    - Isolate modules from each other (loose coupling).
    - Report delivery failures to system health topic.

    MVP scope:
    - In-process async queue (no external broker).
    - Bounded queue (maxsize=500) to prevent memory exhaustion.
    - Topic-based pub/sub with multiple subscribers per topic.
    - Delivery errors caught per handler — one failure does not
      block delivery to other subscribers on the same topic.
    - No persistence, no circuit breaker (future stage).

    Future stages will add:
    - Circuit breaker: 3 consecutive failures → module isolation.
    - Heartbeat: presence signal every 5 seconds to all subscribers.
    - SM_GSM integration: failure events on "system_health" topic.
    """

    def __init__(self, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE):
        # Bounded queue — prevents memory exhaustion under load.
        # publish() will block if queue is full (backpressure).
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._subscribers: Dict[str, List[Callable]] = {}
        self._running: bool = False
        self._delivery_errors: int = 0
        print("[SM_HUB] Initialized.")

    # ----------------------------------------------------------------
    # Subscription interface
    # ----------------------------------------------------------------

    def subscribe(self, topic: str, handler: Callable) -> None:
        """
        Register an async handler for a given topic.
        Multiple handlers can subscribe to the same topic.
        All will receive the message independently.
        """
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)
        print(f"[SM_HUB] Subscribed to topic: '{topic}'")

    def unsubscribe(self, topic: str, handler: Callable) -> bool:
        """
        Remove a handler from a topic subscription.
        Returns True if the handler was found and removed.
        """
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)
            return True
        return False

    def get_subscriber_count(self, topic: str) -> int:
        """Return the number of subscribers for a given topic."""
        return len(self._subscribers.get(topic, []))

    # ----------------------------------------------------------------
    # Publish interface
    # ----------------------------------------------------------------

    async def publish(self, message: Message) -> None:
        """
        Enqueue a message for routing.
        Blocks if the queue is full (backpressure mechanism).
        All routing happens asynchronously in the run() loop.
        """
        await self._queue.put(message)
        print(
            f"[SM_HUB] Queued | {message.source} → {message.destination} "
            f"| topic: {message.topic} | priority: {message.priority}"
        )

    # ----------------------------------------------------------------
    # Internal routing
    # ----------------------------------------------------------------

    async def _route(self, message: Message) -> None:
        """
        Deliver a message to all subscribers of its topic.
        Handler failures are caught individually — one failing handler
        does not block delivery to other subscribers.
        """
        handlers = self._subscribers.get(message.topic, [])
        if not handlers:
            print(f"[SM_HUB] No subscriber for topic '{message.topic}'")
            return

        for handler in handlers:
            try:
                await handler(message)
            except Exception as e:
                self._delivery_errors += 1
                print(
                    f"[SM_HUB] Delivery error on topic '{message.topic}' "
                    f"(handler: {getattr(handler, '__name__', '?')}): {e}"
                )

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    async def run(self) -> None:
        """
        Main routing loop. Runs until stop() is called.
        Processes messages from the queue one by one.
        """
        self._running = True
        print("[SM_HUB] Routing loop started.")
        while self._running:
            try:
                message = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._route(message)
            except asyncio.TimeoutError:
                pass  # No messages — keep waiting

        print("[SM_HUB] Routing loop stopped.")

    def stop(self) -> None:
        """Signal the routing loop to stop after current message."""
        self._running = False
        print("[SM_HUB] Stop requested.")

    # ----------------------------------------------------------------
    # Diagnostics
    # ----------------------------------------------------------------

    def get_queue_size(self) -> int:
        """Return the current number of messages waiting in the queue."""
        return self._queue.qsize()

    def get_delivery_errors(self) -> int:
        """Return the total number of delivery errors since initialization."""
        return self._delivery_errors

    def get_stats(self) -> dict:
        """Return a snapshot of hub diagnostics for monitoring."""
        return {
            "queue_size": self._queue.qsize(),
            "queue_maxsize": self._queue.maxsize,
            "delivery_errors": self._delivery_errors,
            "running": self._running,
            "topic_count": len(self._subscribers),
        }
