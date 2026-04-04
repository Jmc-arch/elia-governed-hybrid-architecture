# tests/test_sm_log.py — ELIA Stage 1

import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stage1.sm_log import SMLog, LogType, LogLevel


class TestSMLog(unittest.TestCase):
    def setUp(self):
        self.mock_syn = MagicMock()
        self.mock_syn.log_event.return_value = True

        self.sm_log = SMLog(syn=self.mock_syn)

        self.sm_log._buffer.clear()
        self.sm_log._active_alerts.clear()
        self.sm_log._satisfaction_history.clear()

    async def test_log_basic_entry(self):
        cid = await self.sm_log.log(
            log_type=LogType.SYSTEM,
            source="TEST_MODULE",
            message="This is a test log message",
            level=LogLevel.INFO,
            data={"key": "value"}
        )

        self.assertIsNotNone(cid)
        self.assertEqual(len(self.sm_log._buffer), 1)
        self.mock_syn.log_event.assert_called_once()

    def test_convenience_methods(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(self.sm_log.log_system("TEST", "System event"))
        loop.run_until_complete(self.sm_log.log_warning("TEST", "Warning message"))
        loop.run_until_complete(self.sm_log.log_error("TEST", "Error occurred"))
        loop.run_until_complete(self.sm_log.log_critical("TEST", "Critical failure"))

        self.assertGreaterEqual(len(self.sm_log._buffer), 4)
        loop.close()

    async def test_log_feedback_and_satisfaction(self):
        await self.sm_log.log_feedback(user_id="user123", value=0.9, context={})
        await self.sm_log.log_feedback(user_id="user123", value=0.3, context={})

        status = self.sm_log.get_satisfaction_alert_status()
        self.assertAlmostEqual(status["average_last_10"], 0.6, places=2)

    async def test_satisfaction_alert_triggers(self):
        for _ in range(12):
            await self.sm_log.log_feedback(user_id="user123", value=0.35, context={})

        status = self.sm_log.get_satisfaction_alert_status()
        self.assertTrue(status["alert_active"])

    async def test_receive_log_event_from_hub(self):
        event = {
            "log_type": "system",
            "source": "SM_HUB",
            "message": "Message from hub",
            "level": "info",
            "data": {},
            "correlation_id": "hub-123"
        }
        await self.sm_log.receive_log_event(event)
        self.assertEqual(len(self.sm_log._buffer), 1)

    def test_health_metrics(self):
        metrics = self.sm_log.get_health_metrics()
        self.assertEqual(metrics["buffer_capacity"], 1000)


if __name__ == "__main__":
    unittest.main()
