# test_sm_hub.py — ELIA Stage 0
# Unit tests for the message bus (SM_HUB)

import asyncio
import unittest

from stage0.sm_hub import Message, SMHub


class TestSMHub(unittest.IsolatedAsyncioTestCase):
    async def test_publish_delivers_message_to_subscriber(self):
        hub = SMHub()
        received = []
        delivered = asyncio.Event()

        async def handler(message):
            received.append(message)
            delivered.set()

        hub.subscribe("system_event", handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(
                Message(
                    source="main",
                    destination="SM_SYN",
                    topic="system_event",
                    payload={"event": "boot_complete"},
                )
            )

            await asyncio.wait_for(delivered.wait(), timeout=2)

            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload, {"event": "boot_complete"})
        finally:
            hub.stop()
            await hub_task

    async def test_publish_reaches_all_subscribers_for_topic(self):
        hub = SMHub()
        deliveries = []
        delivered = asyncio.Event()

        async def handler_one(message):
            deliveries.append(("one", message.topic))
            if len(deliveries) == 2:
                delivered.set()

        async def handler_two(message):
            deliveries.append(("two", message.topic))
            if len(deliveries) == 2:
                delivered.set()

        hub.subscribe("state_transition", handler_one)
        hub.subscribe("state_transition", handler_two)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(
                Message(
                    source="SM_SYN",
                    destination="main",
                    topic="state_transition",
                    payload={"from": "INIT", "to": "STABILIZING"},
                )
            )

            await asyncio.wait_for(delivered.wait(), timeout=2)

            self.assertCountEqual(
                deliveries,
                [("one", "state_transition"), ("two", "state_transition")],
            )
        finally:
            hub.stop()
            await hub_task


if __name__ == "__main__":
    unittest.main()
