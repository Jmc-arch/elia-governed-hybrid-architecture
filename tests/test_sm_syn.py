import tempfile
import unittest
from pathlib import Path

from phase0.el_mem import ELMem
from phase0.sm_syn import SMSyn


class TestSMSyn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_initial_state_is_persisted(self):
        self.assertEqual(self.syn.get_state(), "INIT")
        self.assertEqual(self.memory.atomic_read("system_state"), "INIT")
        self.assertEqual(
            self.memory.atomic_read("system_flags"),
            {"neural_processing": False, "learning_enabled": False},
        )

    def test_valid_transition_updates_state_and_logs_event(self):
        self.assertTrue(self.syn.transition_to("STABILIZING"))

        self.assertEqual(self.syn.get_state(), "STABILIZING")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

        event = self.memory.read_events(limit=1)[0]
        self.assertEqual(event["source"], "SM_SYN")
        self.assertEqual(event["topic"], "state_transition")

    def test_invalid_transition_is_denied(self):
        self.assertFalse(self.syn.transition_to("SHUTDOWN"))
        self.assertEqual(self.syn.get_state(), "INIT")
        self.assertEqual(self.memory.read_events(limit=10), [])

    def test_set_flag_persists_changes(self):
        self.assertTrue(self.syn.set_flag("neural_processing", True))
        self.assertTrue(self.syn.get_flag("neural_processing"))
        self.assertEqual(
            self.memory.atomic_read("system_flags"),
            {"neural_processing": True, "learning_enabled": False},
        )


if __name__ == "__main__":
    unittest.main()
