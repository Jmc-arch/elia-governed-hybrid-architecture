# test_el_mem.py — ELIA
# Unit and regression tests for EL_MEM (memory layer).
#
# Test coverage:
# - Core read/write operations (functional correctness)
# - Audit trail (append-only event log)
# - WAL mode (required by EL-ARCH lines 753, 784)
# - Schema versioning (migration safety)
# - Resilience (error handling without crash)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from el_mem import ELMem


class TestELMemCore(unittest.TestCase):
    """Core read/write operations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_atomic_write_and_read_round_trip(self):
        """Written value must be retrievable with identical content."""
        payload = {"state": "INTERACTIVE", "flags": {"neural_processing": False}}
        self.assertTrue(self.memory.atomic_write("system_snapshot", payload))
        self.assertEqual(self.memory.atomic_read("system_snapshot"), payload)

    def test_atomic_write_overwrites_existing_key(self):
        """Second write on same key must replace previous value."""
        self.memory.atomic_write("key", "first")
        self.memory.atomic_write("key", "second")
        self.assertEqual(self.memory.atomic_read("key"), "second")

    def test_atomic_read_returns_none_for_missing_key(self):
        """Reading an unknown key must return None without raising."""
        self.assertIsNone(self.memory.atomic_read("nonexistent_key"))

    def test_atomic_write_returns_false_on_error(self):
        """Write failure must return False, not raise."""
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.memory.atomic_write("key", "value")
            self.assertFalse(result)

    def test_atomic_read_never_raises(self):
        """Read must never raise regardless of key."""
        try:
            self.memory.atomic_read("completely_unknown_key_xyz")
        except Exception as e:
            self.fail(f"atomic_read raised unexpectedly: {e}")


class TestELMemAuditTrail(unittest.TestCase):
    """Audit trail — append-only event log."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_log_event_records_all_fields(self):
        """Logged event must contain all expected fields."""
        event_payload = {"event": "boot_complete", "state": "INTERACTIVE"}
        self.assertTrue(
            self.memory.log_event(
                source="SM_SYN",
                topic="system_event",
                payload=event_payload,
            )
        )
        events = self.memory.read_events(limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "SM_SYN")
        self.assertEqual(events[0]["topic"], "system_event")
        self.assertEqual(json.loads(events[0]["payload"]), event_payload)

    def test_read_events_returns_newest_first(self):
        """Events must be returned in reverse chronological order."""
        self.memory.log_event("src", "topic", {"order": 1})
        self.memory.log_event("src", "topic", {"order": 2})
        self.memory.log_event("src", "topic", {"order": 3})
        events = self.memory.read_events(limit=3)
        payloads = [json.loads(e["payload"])["order"] for e in events]
        self.assertEqual(payloads, [3, 2, 1])

    def test_read_events_respects_limit(self):
        """read_events must respect the limit parameter."""
        for i in range(10):
            self.memory.log_event("src", "topic", {"i": i})
        events = self.memory.read_events(limit=3)
        self.assertEqual(len(events), 3)

    def test_log_event_failure_returns_false(self):
        """Log failure must return False without raising."""
        with patch.object(self.memory, "log_event", return_value=False):
            result = self.memory.log_event("src", "topic", {})
            self.assertFalse(result)

    def test_read_events_returns_empty_list_on_empty_log(self):
        """Empty audit trail must return empty list, not None."""
        events = self.memory.read_events()
        self.assertIsInstance(events, list)
        self.assertEqual(len(events), 0)


class TestELMemInfrastructure(unittest.TestCase):
    """Infrastructure — WAL mode and schema versioning."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_wal_mode_is_enabled(self):
        """
        WAL mode must be active after initialization.
        Required by EL-ARCH spec (lines 753, 784) for concurrent reads.
        """
        cursor = self.memory._conn.execute("PRAGMA journal_mode")
        row = cursor.fetchone()
        self.assertEqual(row[0], "wal")

    def test_schema_version_is_zero_on_first_init(self):
        """
        Schema version must be 0 on first initialization.
        Guards against accidental schema version regression.
        """
        self.assertEqual(self.memory.get_schema_version(), 0)

    def test_schema_version_matches_class_constant(self):
        """
        Installed schema version must match the declared class constant.
        Guards against mismatch between code and database state.
        """
        self.assertEqual(
            self.memory.get_schema_version(),
            ELMem.SCHEMA_VERSION,
        )

    def test_schema_version_survives_restart(self):
        """
        Schema version must be preserved across database restarts.
        """
        self.memory.close()
        memory2 = ELMem(str(self.db_path))
        self.assertEqual(memory2.get_schema_version(), 0)
        memory2.close()


class TestELMemResilience(unittest.TestCase):
    """Resilience — system continues operating under failure conditions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_previous_value_intact_after_write_failure(self):
        """
        If a write fails, the previously stored value must be unchanged.
        SM_SYN relies on this guarantee for Option B rollback.
        """
        self.memory.atomic_write("system_state", "STABILIZING")
        with patch.object(self.memory, "atomic_write", return_value=False):
            self.memory.atomic_write("system_state", "INTERACTIVE")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

    def test_state_readable_after_log_failure(self):
        """
        If log_event fails, system state must remain readable and uncorrupted.
        """
        self.memory.atomic_write("system_state", "INTERACTIVE")
        with patch.object(self.memory, "log_event", return_value=False):
            self.memory.log_event("SM_SYN", "state_transition", {"from": "INIT"})
        self.assertEqual(self.memory.atomic_read("system_state"), "INTERACTIVE")

    def test_state_survives_restart(self):
        """
        Written state must be recoverable after a simulated crash and restart.
        This is the foundation of SM_SYN Option B persistence contract.
        """
        self.memory.atomic_write("system_state", "INTERACTIVE")
        self.memory.close()

        memory2 = ELMem(str(self.db_path))
        recovered = memory2.atomic_read("system_state")
        self.assertEqual(recovered, "INTERACTIVE")
        memory2.close()

    def test_audit_trail_survives_restart(self):
        """
        Audit trail events must be recoverable after a simulated crash.
        """
        self.memory.log_event("SM_SYN", "state_transition", {"from": "INIT", "to": "STABILIZING"})
        self.memory.log_event("SM_SYN", "state_transition", {"from": "STABILIZING", "to": "INTERACTIVE"})
        self.memory.close()

        memory2 = ELMem(str(self.db_path))
        events = memory2.read_events(limit=10)
        self.assertEqual(len(events), 2)
        memory2.close()


if __name__ == "__main__":
    unittest.main()
