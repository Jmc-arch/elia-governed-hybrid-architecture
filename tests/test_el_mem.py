# test_el_mem.py — ELIA Stage 0
# Unit tests for the memory layer (EL_MEM)

import json
import tempfile
import unittest
from pathlib import Path

from stage0.el_mem import ELMem


class TestELMem(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_atomic_write_and_read_round_trip(self):
        payload = {"state": "INTERACTIVE", "flags": {"neural_processing": False}}

        self.assertTrue(self.memory.atomic_write("system_snapshot", payload))
        self.assertEqual(self.memory.atomic_read("system_snapshot"), payload)

    def test_atomic_read_returns_none_for_missing_key(self):
        self.assertIsNone(self.memory.atomic_read("missing_key"))

    def test_log_event_records_payload(self):
        event_payload = {"event": "boot_complete", "state": "INTERACTIVE"}

        self.assertTrue(
            self.memory.log_event(
                source="main",
                topic="system_event",
                payload=event_payload,
            )
        )

        events = self.memory.read_events(limit=1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "main")
        self.assertEqual(events[0]["topic"], "system_event")
        self.assertEqual(json.loads(events[0]["payload"]), event_payload)


if __name__ == "__main__":
    unittest.main()
