# tests/test_sm_syn.py

import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from stage1.sm_syn import SMSyn
from stage1.el_mem import ELMem


class TestSMSyn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        if hasattr(self.memory, "close"):
            self.memory.close()
        self.temp_dir.cleanup()

    def test_initial_state(self):
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_valid_transition(self):
        self.assertTrue(self.syn.transition_to("STABILIZING"))
        self.assertEqual(self.syn.get_state(), "STABILIZING")

    def test_invalid_transition(self):
        self.assertFalse(self.syn.transition_to("SHUTDOWN"))
        self.assertEqual(self.syn.get_state(), "INIT")

    def test_set_flag(self):
        self.assertTrue(self.syn.set_flag("neural_processing", True))
        self.assertTrue(self.syn.get_flag("neural_processing"))


if __name__ == "__main__":
    unittest.main()
