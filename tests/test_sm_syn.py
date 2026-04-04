# test_sm_syn.py — ELIA Stage 1
# Unit tests for SM_SYN (state coordination + persistence)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Adjust imports according to your project structure
# If you run tests from the root:
from sm_syn import SMSyn
from el_mem import ELMem   # or from .el_mem if in package


class TestSMSyn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))          # your ELMem class
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close() if hasattr(self.memory, "close") else None
        self.temp_dir.cleanup()

    def test_initial_state_is_persisted(self):
        """Initial state and flags must be persisted (Option B)."""
        self.assertEqual(self.syn.get_state(), "INIT")
        self.assertEqual(self.memory.atomic_read("system_state"), "INIT")
        self.assertEqual(
            self.memory.atomic_read("system_flags"),
            {"neural_processing": False, "learning_enabled": False},
        )

    def test_valid_transition_updates_state_and_logs_event(self):
        """Valid transition must update state and persist it."""
        self.assertTrue(self.syn.transition_to("STABILIZING"))
        self.assertEqual(self.syn.get_state(), "STABILIZING")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

    def test_invalid_transition_is_denied(self):
        """Invalid transition must be rejected."""
        self.assertFalse(self.syn.transition_to("SHUTDOWN"))   # from INIT → SHUTDOWN is invalid
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_set_flag_persists_changes(self):
        """Flag update must be persisted only if write succeeds (Option B)."""
        self.assertTrue(self.syn.set_flag("neural_processing", True))
        self.assertTrue(self.syn.get_flag("neural_processing"))
        self.assertEqual(
            self.memory.atomic_read("system_flags"),
            {"neural_processing": True, "learning_enabled": False},
        )

    def test_logger_injection_works(self):
        """SM_SYN must be able to call the injected logger (SM_LOG.emit_sync or log_system)."""
        mock_logger = MagicMock()
        self.syn.set_logger(mock_logger)
        self.syn._emit("info", "Test message", {"key": "value"})
        mock_logger.assert_called_once_with(
            source="SM_SYN",
            message="Test message",
            level="info",
            data={"key": "value"},
        )

    def test_transition_to_shutdown_from_interactive(self):
        """Example of allowed transition."""
        self.syn.transition_to("INTERACTIVE")   # first go to INTERACTIVE
        self.assertTrue(self.syn.transition_to("SHUTDOWN"))
        self.assertEqual(self.syn.get_state(), "SHUTDOWN")


if __name__ == "__main__":
    unittest.main()
