# test_sm_log.py — ELIA Stage 1
# Unit tests for the Unified Logging System (SM_LOG)

import unittest
from unittest.mock import MagicMock, patch
from collections import deque
import asyncio

from sm_log import SMLog, LogType, LogLevel


class TestSMLog(unittest.TestCase):
    def setUp(self):
        # Mock SM_SYN
        self.mock_syn = MagicMock()
        self.mock_syn.log_event.return_value = True  # simulate successful persistence

        self.sm_log = SMLog(syn=self.mock_syn)

        # Clear buffers for clean tests
        self.sm_log._buffer.clear()
        self.sm_log._active_alerts.clear()
        self.sm_log._satisfaction_history.clear()

    def test_initialization(self):
        """SM_LOG should initialize correctly with SM_SYN."""
        self.assertIsInstance(self.sm_log._buffer, deque)
        self.assertEqual(len(self.sm_log._buffer), 0)
        self.assertEqual(self.sm_log.BUFFER_MAX_SIZE, 1000)

    async def test_log_basic_entry(self):
        """Basic log should write to buffer and call SM_SYN."""
        cid = await self.sm_log.log(
            log_type=LogType.SYSTEM,
            source="TEST",
            message="Test message",
            level=LogLevel.INFO,
            data={"key": "value"}
        )

        self.assertIsNotNone(cid)
        self.assertEqual(len(self.sm_log._buffer), 1)
        entry = self.sm_log._buffer[0]
        self.assertEqual(entry["source"], "TEST")
        self.assertEqual(entry["message"], "Test message")

        # Check that SM_SYN was called non-blockingly
        self.mock_syn.log_event.assert_called_once()

    def test_convenience_methods(self):
        """All convenience methods should work."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(self.sm_log.log_system("TEST", "System event"))
        loop.run_until_complete(self.sm_log.log_warning("TEST", "Warning"))
        loop.run_until_complete(self.sm_log.log_error("TEST", "Error"))
        loop.run_until_complete(self.sm_log.log_critical("TEST", "Critical"))

        self.assertEqual(len(self.sm_log._buffer), 4)
        loop.close()

    async def test_log_feedback_and_satisfaction(self):
        """Feedback should update satisfaction history."""
        await self.sm_log.log_feedback(user_id="user123", value=0.9, context={"conv": 1})
        await self.sm_log.log_feedback(user_id="user123", value=0.3, context={"conv": 2})

        status = self.sm_log.get_satisfaction_alert_status()
        self.assertEqual(status["average_last_10"], 0.6)
        self.assertFalse(status["alert_active"])

    async def test_satisfaction_alert_trigger(self):
        """Should trigger alert when average < 0.4 over 10 cycles."""
        for i in range(12):
            await self.sm_log.log_feedback(user_id="user123", value=0.3, context={i: i})

        status = self.sm_log.get_satisfaction_alert_status()
        self.assertTrue(status["alert_active"])
        self.assertLess(status["average_last_10"], 0.4)

    def test_get_alert_status_and_clear(self):
        """Critical logs should create alerts."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.sm_log.log_critical("TEST", "Critical alert"))
        loop.close()

        alerts = self.sm_log.get_alert_status()
        self.assertEqual(len(alerts), 1)

        alert_id = alerts[0]["alert_id"]
        self.assertTrue(self.sm_log.clear_alert(alert_id))
        self.assertEqual(len(self.sm_log.get_alert_status()), 0)

    async def test_receive_log_event_from_hub(self):
        """Should handle events coming from SM_HUB."""
        event = {
            "log_type": "system",
            "source": "SM_HUB",
            "message": "Test from hub",
            "level": "info",
            "data": {"test": True},
            "correlation_id": "12345"
        }
        await self.sm_log.receive_log_event(event)

        self.assertEqual(len(self.sm_log._buffer), 1)
        entry = self.sm_log._buffer[0]
        self.assertEqual(entry["source"], "SM_HUB")
        self.assertEqual(entry["correlation_id"], "12345")

    def test_buffer_query_and_filter(self):
        """Buffer query and correlation filter should work."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(self.sm_log.log_system("TEST1", "Message 1"))
        loop.run_until_complete(self.sm_log.log_warning("TEST2", "Message 2"))

        loop.close()

        # Query by source
        results = self.sm_log.query_buffer(source="TEST1")
        self.assertEqual(len(results), 1)

        # Filter by correlation (simulate)
        cid = self.sm_log._buffer[0]["correlation_id"]
        filtered = self.sm_log.filter_by_correlation(cid)
        self.assertEqual(len(filtered), 1)

    def test_health_metrics(self):
        """Health metrics should return correct buffer info."""
        metrics = self.sm_log.get_health_metrics()
        self.assertEqual(metrics["buffer_capacity"], 1000)
        self.assertEqual(metrics["buffer_size"], 0)


if __name__ == "__main__":
    unittest.main()
