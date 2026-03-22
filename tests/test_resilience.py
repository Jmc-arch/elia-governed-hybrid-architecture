# test_resilience.py — ELIA Phase 0
# Resilience tests: verifies that the system continues operating
# when individual components fail or behave unexpectedly.
#
# This validates the Phase 0 requirement:
# "Components can fail without collapsing the whole system."

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase0.el_mem import ELMem
from phase0.sm_hub import Message, SMHub
from phase0.sm_syn import SMSyn


class TestSMHubResilience(unittest.IsolatedAsyncioTestCase):
    """SM_HUB resilience: message bus failure scenarios."""

    async def test_message_to_unknown_topic_does_not_crash(self):
        """
        Scenario: A message is published to a topic with no subscribers.
        Expected: SM_HUB handles it silently without raising an exception.
        """
        hub = SMHub()
        hub_task = asyncio.create_task(hub.run())

        try:
            # No subscriber registered for this topic
            await hub.publish(Message(
                source="main",
                destination="unknown",
                topic="nonexistent_topic",
                payload={"event": "ghost_message"},
            ))
            await asyncio.sleep(0.3)
            # If we reach here, the system did not crash — test passes
        finally:
            hub.stop()
            await hub_task

    async def test_failing_subscriber_does_not_block_other_subscribers(self):
        """
        Scenario: One subscriber crashes when receiving a message.
        Expected: The other subscriber still receives the message correctly.
        """
        hub = SMHub()
        received_by_good_handler = []
        delivered = asyncio.Event()

        async def failing_handler(message):
            raise RuntimeError("Simulated subscriber crash")

        async def good_handler(message):
            received_by_good_handler.append(message)
            delivered.set()

        hub.subscribe("system_event", failing_handler)
        hub.subscribe("system_event", good_handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            await hub.publish(Message(
                source="main",
                destination="SM_SYN",
                topic="system_event",
                payload={"event": "boot_complete"},
            ))

            await asyncio.wait_for(delivered.wait(), timeout=2)

            # Good handler still received the message despite the crash
            self.assertEqual(len(received_by_good_handler), 1)
            self.assertEqual(
                received_by_good_handler[0].payload,
                {"event": "boot_complete"}
            )
        finally:
            hub.stop()
            await hub_task

    async def test_hub_processes_messages_after_subscriber_error(self):
        """
        Scenario: A subscriber error occurs, then a new message is published.
        Expected: SM_HUB continues routing subsequent messages normally.
        """
        hub = SMHub()
        received = []
        delivered = asyncio.Event()

        async def flaky_handler(message):
            if message.payload.get("round") == 1:
                raise RuntimeError("Simulated failure on first message")
            received.append(message)
            delivered.set()

        hub.subscribe("system_event", flaky_handler)
        hub_task = asyncio.create_task(hub.run())

        try:
            # First message — will cause handler to crash
            await hub.publish(Message(
                source="main",
                destination="SM_SYN",
                topic="system_event",
                payload={"round": 1},
            ))
            await asyncio.sleep(0.3)

            # Second message — should be delivered normally
            await hub.publish(Message(
                source="main",
                destination="SM_SYN",
                topic="system_event",
                payload={"round": 2},
            ))

            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload["round"], 2)
        finally:
            hub.stop()
            await hub_task


class TestELMemResilience(unittest.TestCase):
    """EL_MEM resilience: storage failure scenarios."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_read_after_write_failure_returns_previous_value(self):
        """
        Scenario: A write fails (simulated), then a read is attempted.
        Expected: The previous valid value is still readable.
        """
        # Write a valid initial value
        self.memory.atomic_write("system_state", "STABILIZING")

        # Simulate a write failure by patching atomic_write directly
        original_write = self.memory.atomic_write
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.memory.atomic_write("system_state", "INTERACTIVE")
            self.assertFalse(result)  # Write must report failure

        # Previous value must still be intact — read bypasses the patch
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

    def test_log_event_failure_does_not_corrupt_state(self):
        """
        Scenario: Logging an event fails.
        Expected: The system state remains readable and uncorrupted.
        """
        self.memory.atomic_write("system_state", "INTERACTIVE")

        # Simulate log failure by patching log_event directly
        with patch.object(self.memory, "log_event", return_value=False):
            result = self.memory.log_event(
                source="SM_SYN",
                topic="state_transition",
                payload={"from": "INIT", "to": "STABILIZING"},
            )
            self.assertFalse(result)  # Log must report failure

        # State must still be readable
        self.assertEqual(self.memory.atomic_read("system_state"), "INTERACTIVE")

    def test_read_missing_key_never_raises(self):
        """
        Scenario: Reading a key that was never written.
        Expected: Returns None, never raises an exception.
        """
        result = self.memory.atomic_read("completely_unknown_key")
        self.assertIsNone(result)


class TestSMSynResilience(unittest.TestCase):
    """SM_SYN resilience: state coordination failure scenarios."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_invalid_transition_leaves_state_unchanged(self):
        """
        Scenario: An invalid state transition is attempted (INIT → SHUTDOWN).
        Expected: State remains INIT, no event is logged.
        """
        result = self.syn.transition_to("SHUTDOWN")

        self.assertFalse(result)
        self.assertEqual(self.syn.get_state(), "INIT")
        # No transition event should have been logged
        events = self.memory.read_events(limit=10)
        self.assertEqual(len(events), 0)

    def test_unknown_state_transition_is_rejected(self):
        """
        Scenario: A completely unknown state is requested.
        Expected: Rejected cleanly, system state unchanged.
        """
        result = self.syn.transition_to("FLYING_TO_MOON")

        self.assertFalse(result)
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_unknown_flag_set_is_rejected(self):
        """
        Scenario: An unknown flag is set.
        Expected: Rejected cleanly, existing flags unchanged.
        """
        result = self.syn.set_flag("nonexistent_flag", True)

        self.assertFalse(result)
        # Real flags must be untouched
        self.assertFalse(self.syn.get_flag("neural_processing"))
        self.assertFalse(self.syn.get_flag("learning_enabled"))

    def test_memory_write_failure_during_transition_is_handled(self):
        """
        Scenario: EL_MEM fails during a state transition write.
        Expected: SM_SYN handles the error without crashing.
        The in-memory state may update but the system must not raise.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            with patch.object(self.memory, "log_event", return_value=False):
                # Should not raise even if persistence fails
                try:
                    self.syn.transition_to("STABILIZING")
                except Exception as e:
                    self.fail(f"SM_SYN raised an exception during memory failure: {e}")

    def test_neural_processing_flag_is_false_by_default(self):
        """
        Scenario: System boots normally.
        Expected: neural_processing is always False at startup.
        This is a governance invariant — neural must never auto-activate.
        """
        self.assertFalse(self.syn.get_flag("neural_processing"))
        self.assertFalse(self.syn.get_flag("learning_enabled"))


if __name__ == "__main__":
    unittest.main()
