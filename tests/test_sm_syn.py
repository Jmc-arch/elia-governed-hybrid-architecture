# test_sm_syn.py — ELIA
# Unit, resilience and recovery tests for SM_SYN (state coordination).
#
# Test coverage:
# - State machine (transitions, invariants)
# - Flag management (governance invariants)
# - Option B persistence contract (pessimistic rollback)
# - Logging abstraction (set_logger)
# - Public log_event interface (SM_LOG entry point)
# - Recovery after simulated crash
# - Governance invariants (neural_processing never auto-activates)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from el_mem import ELMem
from sm_syn import SMSyn, VALID_STATES, TRANSITIONS, DEFAULT_FLAGS


class TestSMSynStateMachine(unittest.TestCase):
    """State machine — transitions and invariants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_initial_state_is_init(self):
        """System must start in INIT state."""
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_initial_state_is_persisted(self):
        """Initial state must be written to EL_MEM at startup."""
        self.assertEqual(self.memory.atomic_read("system_state"), "INIT")

    def test_valid_transition_succeeds(self):
        """Valid transition must succeed and update in-memory state."""
        self.assertTrue(self.syn.transition_to("STABILIZING"))
        self.assertEqual(self.syn.get_state(), "STABILIZING")

    def test_valid_transition_is_persisted(self):
        """Valid transition must be written to EL_MEM."""
        self.syn.transition_to("STABILIZING")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

    def test_valid_transition_logs_audit_event(self):
        """Valid transition must create an audit trail entry."""
        self.syn.transition_to("STABILIZING")
        events = self.memory.read_events(limit=10)
        transition_events = [e for e in events if e["topic"] == "state_transition"]
        self.assertGreater(len(transition_events), 0)

    def test_invalid_transition_is_denied(self):
        """Transition not in TRANSITIONS map must be denied."""
        self.assertFalse(self.syn.transition_to("SHUTDOWN"))
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_invalid_transition_leaves_no_audit_event(self):
        """Denied transition must not create an audit trail entry."""
        self.syn.transition_to("SHUTDOWN")
        events = self.memory.read_events(limit=10)
        self.assertEqual(len(events), 0)

    def test_unknown_state_is_rejected(self):
        """Completely unknown state must be rejected."""
        self.assertFalse(self.syn.transition_to("FLYING"))
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_full_valid_path(self):
        """Full path INIT → STABILIZING → INTERACTIVE must succeed."""
        self.assertTrue(self.syn.transition_to("STABILIZING"))
        self.assertTrue(self.syn.transition_to("INTERACTIVE"))
        self.assertEqual(self.syn.get_state(), "INTERACTIVE")

    def test_shutdown_is_terminal(self):
        """No transition is allowed from SHUTDOWN state."""
        self.syn.transition_to("STABILIZING")
        self.syn.transition_to("SHUTDOWN")
        self.assertFalse(self.syn.transition_to("INIT"))
        self.assertEqual(self.syn.get_state(), "SHUTDOWN")

    def test_snapshot_contains_required_fields(self):
        """System snapshot must contain state, flags, and timestamp."""
        snapshot = self.syn.get_system_snapshot()
        self.assertIn("state", snapshot)
        self.assertIn("flags", snapshot)
        self.assertIn("timestamp", snapshot)

    def test_snapshot_flags_are_independent_copy(self):
        """Modifying snapshot must not affect internal flags."""
        snapshot = self.syn.get_system_snapshot()
        snapshot["flags"]["neural_processing"] = True
        self.assertFalse(self.syn.get_flag("neural_processing"))


class TestSMSynFlags(unittest.TestCase):
    """Flag management — governance invariants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_neural_processing_is_false_by_default(self):
        """
        neural_processing must be False at startup.
        This is a governance invariant — neural must NEVER auto-activate.
        """
        self.assertFalse(self.syn.get_flag("neural_processing"))

    def test_learning_enabled_is_false_by_default(self):
        """learning_enabled must be False at startup."""
        self.assertFalse(self.syn.get_flag("learning_enabled"))

    def test_initial_flags_are_persisted(self):
        """Initial flags must be written to EL_MEM at startup."""
        flags = self.memory.atomic_read("system_flags")
        self.assertFalse(flags["neural_processing"])
        self.assertFalse(flags["learning_enabled"])

    def test_set_flag_updates_in_memory_state(self):
        """set_flag must update the in-memory flag value."""
        self.assertTrue(self.syn.set_flag("neural_processing", True))
        self.assertTrue(self.syn.get_flag("neural_processing"))

    def test_set_flag_persists_to_el_mem(self):
        """set_flag must persist the updated flags to EL_MEM."""
        self.syn.set_flag("neural_processing", True)
        flags = self.memory.atomic_read("system_flags")
        self.assertTrue(flags["neural_processing"])

    def test_set_flag_logs_audit_event(self):
        """set_flag must create an audit trail entry."""
        self.syn.set_flag("neural_processing", True)
        events = self.memory.read_events(limit=10)
        flag_events = [e for e in events if e["topic"] == "flag_update"]
        self.assertGreater(len(flag_events), 0)

    def test_unknown_flag_is_rejected(self):
        """Setting an unknown flag must return False."""
        self.assertFalse(self.syn.set_flag("nonexistent_flag", True))

    def test_get_unknown_flag_returns_false(self):
        """Reading an unknown flag must return False."""
        self.assertFalse(self.syn.get_flag("nonexistent_flag"))


class TestSMSynOptionB(unittest.TestCase):
    """Option B persistence contract — pessimistic rollback."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_transition_aborted_if_persistence_fails(self):
        """
        If EL_MEM write fails, transition must be rolled back.
        In-memory state must remain unchanged.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.syn.transition_to("STABILIZING")
        self.assertFalse(result)
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_transition_succeeds_when_persistence_recovers(self):
        """After DB recovery, a previously failed transition must succeed."""
        with patch.object(self.memory, "atomic_write", return_value=False):
            self.syn.transition_to("STABILIZING")
        self.assertEqual(self.syn.get_state(), "INIT")

        result = self.syn.transition_to("STABILIZING")
        self.assertTrue(result)
        self.assertEqual(self.syn.get_state(), "STABILIZING")

    def test_flag_update_aborted_if_persistence_fails(self):
        """If EL_MEM write fails, flag must remain at previous value."""
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.syn.set_flag("neural_processing", True)
        self.assertFalse(result)
        self.assertFalse(self.syn.get_flag("neural_processing"))

    def test_neural_processing_cannot_activate_without_persistence(self):
        """
        neural_processing must stay False if EL_MEM is unavailable.
        Critical governance invariant: neural must never activate
        in an unaudited, unpersisted state.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            self.syn.set_flag("neural_processing", True)
        self.assertFalse(self.syn.get_flag("neural_processing"))

    def test_transition_committed_even_if_audit_log_fails(self):
        """
        If atomic_write succeeds but log_event fails,
        the transition must still be committed (write = auditable).
        Only the event log entry is missing — not a rollback trigger.
        """
        with patch.object(self.memory, "log_event", return_value=False):
            result = self.syn.transition_to("STABILIZING")
        self.assertTrue(result)
        self.assertEqual(self.syn.get_state(), "STABILIZING")


class TestSMSynLoggingAbstraction(unittest.TestCase):
    """Logging abstraction — MVP to Stage 1 bridge."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_set_logger_injects_callable(self):
        """set_logger must accept a callable without raising."""
        mock_logger = MagicMock()
        try:
            self.syn.set_logger(mock_logger)
        except Exception as e:
            self.fail(f"set_logger raised unexpectedly: {e}")

    def test_emit_routes_to_logger_after_injection(self):
        """After set_logger, _emit must call the injected logger."""
        mock_logger = MagicMock()
        self.syn.set_logger(mock_logger)
        self.syn._emit("info", "test message")
        mock_logger.assert_called()

    def test_emit_never_raises(self):
        """_emit must never raise, even if the logger itself raises."""
        def broken_logger(**kwargs):
            raise RuntimeError("Logger crashed")

        self.syn.set_logger(broken_logger)
        try:
            self.syn._emit("info", "this should not crash SM_SYN")
        except Exception as e:
            self.fail(f"_emit raised unexpectedly: {e}")

    def test_log_event_public_interface(self):
        """Public log_event must persist entries via SM_SYN."""
        result = self.syn.log_event(
            source="SM_LOG",
            topic="log.system",
            payload={"message": "test log entry"},
        )
        self.assertTrue(result)
        events = self.memory.read_events(limit=5)
        log_events = [e for e in events if e["topic"] == "log.system"]
        self.assertEqual(len(log_events), 1)


class TestSMSynRecovery(unittest.TestCase):
    """Recovery — state persistence across simulated restarts."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_survives_restart(self):
        """State must be recoverable from EL_MEM after a simulated crash."""
        memory1 = ELMem(self.db_path)
        syn1 = SMSyn(memory=memory1)
        syn1.transition_to("STABILIZING")
        syn1.transition_to("INTERACTIVE")
        memory1.close()

        memory2 = ELMem(self.db_path)
        recovered = memory2.atomic_read("system_state")
        self.assertEqual(recovered, "INTERACTIVE")
        memory2.close()

    def test_flags_survive_restart(self):
        """Flags must be recoverable from EL_MEM after a simulated crash."""
        memory1 = ELMem(self.db_path)
        syn1 = SMSyn(memory=memory1)
        syn1.transition_to("STABILIZING")
        syn1.transition_to("INTERACTIVE")
        syn1.set_flag("neural_processing", True)
        memory1.close()

        memory2 = ELMem(self.db_path)
        flags = memory2.atomic_read("system_flags")
        self.assertTrue(flags["neural_processing"])
        memory2.close()

    def test_audit_trail_survives_restart(self):
        """Audit trail must be fully recoverable after a simulated crash."""
        memory1 = ELMem(self.db_path)
        syn1 = SMSyn(memory=memory1)
        syn1.transition_to("STABILIZING")
        syn1.transition_to("INTERACTIVE")
        syn1.transition_to("MAINTENANCE")
        memory1.close()

        memory2 = ELMem(self.db_path)
        events = memory2.read_events(limit=20)
        transition_events = [e for e in events if e["topic"] == "state_transition"]
        self.assertGreaterEqual(len(transition_events), 3)
        memory2.close()


if __name__ == "__main__":
    unittest.main()
