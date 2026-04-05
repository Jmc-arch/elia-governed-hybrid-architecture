# test_sm_hub.py — ELIA
# Unit and resilience tests for SM_HUB (central message bus).
#
# Test coverage:
# - Message routing (functional correctness)
# - Multi-subscriber delivery
# - Message validation (priority)
# - Resilience (failing handlers, unknown topics)
# - Queue management (bounded queue)
# - Diagnostics (stats, error tracking)

import asyncio
import unittest

from sm_hub import Message, SMHub, VALID_PRIORITIES


class TestMessage(unittest.TestCase):
    """Message dataclass validation."""

    def test_message_auto_generates_correlation_id(self):
        """Messages without explicit correlation_id must get one automatically."""
        msg = Message(source="A", destination="B", topic="test", payload={})
        self.assertIsNotNone(msg.correlation_id)
        self.assertGreater(len(msg.correlation_id), 0)

    def test_message_correlation_ids_are_unique(self):
        """Each message must have a distinct correlation_id."""
        msg1 = Message(source="A", destination="B", topic="test", payload={})
        msg2 = Message(source="A", destination="B", topic="test", payload={})
        self.assertNotEqual(msg1.correlation_id, msg2.correlation_id)

    def test_message_auto_generates_timestamp(self):
        """Messages must have a UTC timestamp set automatically."""
        msg = Message(source="A", destination="B", topic="test", payload={})
        self.assertIsNotNone(msg.timestamp)
        self.assertIn("T", msg.timestamp)  # ISO format contains 'T'

    def test_message_default_priority_is_normal(self):
        """Default priority must be 'normal' per EL-ARCH spec."""
        msg = Message(source="A", destination="B", topic="test", payload={})
        self.assertEqual(msg.priority, "normal")

    def test_message_rejects_invalid_priority(self):
        """Invalid priority must raise ValueError."""
        with self.assertRaises(ValueError):
            Message(source="A", destination="B", topic="test",
                    payload={}, priority="urgent")

    def test_all_valid_priorities_accepted(self):
        """All EL-ARCH defined priorities must be accepted."""
        for priority in VALID_PRIORITIES:
            msg = Message(source="A", destination="B", topic="test",
                          payload={}, priority=priority)
            self.assertEqual(msg.priority, priority)


class TestSMHubRouting(unittest.IsolatedAsyncioTestCase):
    """Core message routing."""

    async def test_publish_delivers_to_subscriber(self):
        """A published message must be delivered to its topic subscriber."""
        hub = SMHub()
        received = []
        delivered = asyncio.Event()

        async def handler(message):
            received.append(message)
            delivered.set()

        hub.subscribe("system_event", handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="main", destination="SM_SYN",
                topic="system_event",
                payload={"event": "boot_complete"},
            ))
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload, {"event": "boot_complete"})
        finally:
            hub.stop()
            await hub_task

    async def test_publish_delivers_to_all_subscribers_on_topic(self):
        """All subscribers on a topic must receive the message."""
        hub = SMHub()
        deliveries = []
        all_delivered = asyncio.Event()

        async def handler_a(message):
            deliveries.append("A")
            if len(deliveries) == 2:
                all_delivered.set()

        async def handler_b(message):
            deliveries.append("B")
            if len(deliveries) == 2:
                all_delivered.set()

        hub.subscribe("state_transition", handler_a)
        hub.subscribe("state_transition", handler_b)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="SM_SYN", destination="main",
                topic="state_transition",
                payload={"from": "INIT", "to": "STABILIZING"},
            ))
            await asyncio.wait_for(all_delivered.wait(), timeout=2)
            self.assertCountEqual(deliveries, ["A", "B"])
        finally:
            hub.stop()
            await hub_task

    async def test_message_to_unknown_topic_does_not_crash(self):
        """
        Publishing to a topic with no subscribers must not raise.
        SM_HUB must handle it silently and continue running.
        """
        hub = SMHub()
        hub_task = asyncio.create_task(hub.run())
        try:
            await hub.publish(Message(
                source="main", destination="unknown",
                topic="nonexistent_topic",
                payload={"event": "ghost"},
            ))
            await asyncio.sleep(0.3)
            # If we reach here, no crash occurred
        finally:
            hub.stop()
            await hub_task

    async def test_correlation_id_preserved_through_routing(self):
        """Correlation ID must be preserved end-to-end through routing."""
        hub = SMHub()
        received = []
        delivered = asyncio.Event()

        async def handler(message):
            received.append(message)
            delivered.set()

        hub.subscribe("test_topic", handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            msg = Message(
                source="A", destination="B",
                topic="test_topic",
                payload={},
                correlation_id="trace-abc-123",
            )
            await hub.publish(msg)
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(received[0].correlation_id, "trace-abc-123")
        finally:
            hub.stop()
            await hub_task


class TestSMHubResilience(unittest.IsolatedAsyncioTestCase):
    """Resilience — SM_HUB continues operating under failure conditions."""

    async def test_failing_handler_does_not_block_other_handlers(self):
        """
        If one subscriber raises, the other must still receive the message.
        Partial failure must not block delivery to healthy subscribers.
        """
        hub = SMHub()
        received_by_good = []
        delivered = asyncio.Event()

        async def failing_handler(message):
            raise RuntimeError("Simulated handler crash")

        async def good_handler(message):
            received_by_good.append(message)
            delivered.set()

        hub.subscribe("system_event", failing_handler)
        hub.subscribe("system_event", good_handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="main", destination="SM_SYN",
                topic="system_event",
                payload={"event": "boot_complete"},
            ))
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(len(received_by_good), 1)
        finally:
            hub.stop()
            await hub_task

    async def test_hub_continues_routing_after_handler_error(self):
        """
        After a handler error, subsequent messages must be routed normally.
        SM_HUB must not enter a broken state after a delivery failure.
        """
        hub = SMHub()
        received = []
        delivered = asyncio.Event()

        async def flaky_handler(message):
            if message.payload.get("round") == 1:
                raise RuntimeError("Simulated failure on round 1")
            received.append(message)
            delivered.set()

        hub.subscribe("system_event", flaky_handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="main", destination="SM_SYN",
                topic="system_event", payload={"round": 1},
            ))
            await asyncio.sleep(0.3)

            await hub.publish(Message(
                source="main", destination="SM_SYN",
                topic="system_event", payload={"round": 2},
            ))
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload["round"], 2)
        finally:
            hub.stop()
            await hub_task

    async def test_delivery_errors_are_counted(self):
        """Handler failures must increment the delivery error counter."""
        hub = SMHub()

        async def always_fails(message):
            raise RuntimeError("Always fails")

        hub.subscribe("test_topic", always_fails)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="A", destination="B",
                topic="test_topic", payload={},
            ))
            await asyncio.sleep(0.3)
            self.assertGreater(hub.get_delivery_errors(), 0)
        finally:
            hub.stop()
            await hub_task


class TestSMHubQueueManagement(unittest.IsolatedAsyncioTestCase):
    """Queue management — bounded queue behavior."""

    async def test_queue_size_reflects_pending_messages(self):
        """Queue size must reflect the number of unprocessed messages."""
        hub = SMHub(queue_maxsize=10)

        async def slow_handler(message):
            await asyncio.sleep(10)  # Never completes in test

        hub.subscribe("test_topic", slow_handler)

        # Publish one message without starting the routing loop
        await hub.publish(Message(
            source="A", destination="B",
            topic="test_topic", payload={},
        ))
        self.assertEqual(hub.get_queue_size(), 1)

    def test_stats_returns_all_expected_fields(self):
        """get_stats() must return all required diagnostic fields."""
        hub = SMHub()
        stats = hub.get_stats()
        self.assertIn("queue_size", stats)
        self.assertIn("queue_maxsize", stats)
        self.assertIn("delivery_errors", stats)
        self.assertIn("running", stats)
        self.assertIn("topic_count", stats)

    def test_custom_queue_maxsize_is_respected(self):
        """Custom maxsize must be stored and reported correctly."""
        hub = SMHub(queue_maxsize=42)
        self.assertEqual(hub.get_stats()["queue_maxsize"], 42)

    def test_subscribe_and_unsubscribe(self):
        """Handler must be removable after subscription."""
        hub = SMHub()

        async def handler(message):
            pass

        hub.subscribe("test_topic", handler)
        self.assertEqual(hub.get_subscriber_count("test_topic"), 1)

        removed = hub.unsubscribe("test_topic", handler)
        self.assertTrue(removed)
        self.assertEqual(hub.get_subscriber_count("test_topic"), 0)


if __name__ == "__main__":
    unittest.main()
