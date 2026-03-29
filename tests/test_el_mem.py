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

    def test_wal_mode_is_enabled(self):
        """
        Scenario: EL_MEM initializes with WAL mode.
        Expected: journal_mode returns 'wal' — required by EL-ARCH spec.
        WAL mode allows concurrent reads during writes, essential for Stage 1+
        where SM_LOG writes in parallel with other modules.
        """
        cursor = self.memory._conn.execute("PRAGMA journal_mode")
        row = cursor.fetchone()
        self.assertEqual(row[0], "wal")

    def test_schema_version_is_zero(self):
        """
        Scenario: EL_MEM initializes for the first time.
        Expected: schema_version table contains version 0 (Stage 0 schema).
        This version number must be incremented when new tables are added
        in Stage 1+ to enable safe schema migrations.
        """
        version = self.memory.get_schema_version()
        self.assertEqual(version, 0)

    def test_schema_version_matches_class_constant(self):
        """
        Scenario: Installed schema version matches the declared constant.
        Expected: get_schema_version() == ELMem.SCHEMA_VERSION.
        Guards against accidental constant/migration mismatch.
        """
        self.assertEqual(
            self.memory.get_schema_version(),
            ELMem.SCHEMA_VERSION
        )


if __name__ == "__main__":
    unittest.main()
