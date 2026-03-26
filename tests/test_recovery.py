# test_recovery.py — ELIA Stage 0
# Recovery tests: verifies that the system correctly restores its state
# after a simulated crash, and validates Option B persistence contract.
#
# Option B contract: "No persistence = no transition"
# An unpersisted state is an unaudited state — not allowed in Elia.

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stage0.el_mem import ELMem
from stage0.sm_syn import SMSyn


class TestOptionBPersistenceContract(unittest.TestCase):
    """
    Validates the Option B architectural decision:
    SM_SYN must roll back any transition that EL_MEM cannot persist.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_elia.db"
        self.memory = ELMem(str(self.db_path))
        self.syn = SMSyn(memory=self.memory)

    def tearDown(self):
        self.memory.close()
        self.temp_dir.cleanup()

    def test_transition_aborted_if_persistence_fails(self):
        """
        Scenario: EL_MEM write fails during a valid transition.
        Expected (Option B): transition is rejected, state stays at INIT.
        The in-memory state must NOT advance if persistence failed.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.syn.transition_to("STABILIZING")

        self.assertFalse(result)
        # In-memory state must be rolled back
        self.assertEqual(self.syn.get_state(), "INIT")
        # DB must also still show INIT (from initialization)
        self.assertEqual(self.memory.atomic_read("system_state"), "INIT")

    def test_transition_succeeds_when_persistence_recovers(self):
        """
        Scenario: First transition fails (DB down), then DB recovers.
        Expected: Second attempt succeeds and state advances correctly.
        """
        # First attempt — DB down
        with patch.object(self.memory, "atomic_write", return_value=False):
            result_1 = self.syn.transition_to("STABILIZING")
        self.assertFalse(result_1)
        self.assertEqual(self.syn.get_state(), "INIT")

        # Second attempt — DB recovered
        result_2 = self.syn.transition_to("STABILIZING")
        self.assertTrue(result_2)
        self.assertEqual(self.syn.get_state(), "STABILIZING")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")

    def test_flag_update_aborted_if_persistence_fails(self):
        """
        Scenario: EL_MEM write fails during a flag update.
        Expected (Option B): flag stays at previous value.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            result = self.syn.set_flag("neural_processing", True)

        self.assertFalse(result)
        # Flag must remain False
        self.assertFalse(self.syn.get_flag("neural_processing"))

    def test_neural_processing_cannot_activate_without_persistence(self):
        """
        Scenario: Attempt to activate neural processing while DB is down.
        Expected: neural_processing stays False — governance invariant preserved.
        This is critical: neural must NEVER activate in an unaudited state.
        """
        with patch.object(self.memory, "atomic_write", return_value=False):
            self.syn.set_flag("neural_processing", True)

        # Governance invariant: neural processing must still be False
        self.assertFalse(self.syn.get_flag("neural_processing"))
        print("[TEST] Governance invariant confirmed: neural_processing=False without persistence.")

    def test_log_failure_after_successful_write_does_not_rollback(self):
        """
        Scenario: atomic_write succeeds but log_event fails.
        Expected: transition is still committed (write = auditable).
        Only the audit log entry is missing — not a rollback trigger.
        """
        with patch.object(self.memory, "log_event", return_value=False):
            result = self.syn.transition_to("STABILIZING")

        # Transition must succeed — write was OK
        self.assertTrue(result)
        self.assertEqual(self.syn.get_state(), "STABILIZING")
        self.assertEqual(self.memory.atomic_read("system_state"), "STABILIZING")


class TestRecoveryAfterCrash(unittest.TestCase):
    """
    Recovery tests: verifies that the system correctly restores
    its last known state from EL_MEM after a simulated crash.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_elia.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_survives_restart(self):
        """
        Scenario: System boots, transitions to INTERACTIVE, then crashes.
        A new instance boots and reads state from the same DB.
        Expected: New instance recovers INTERACTIVE from EL_MEM.
        """
        # --- Stage 0: Normal boot and operation ---
        memory_1 = ELMem(self.db_path)
        syn_1 = SMSyn(memory=memory_1)
        syn_1.transition_to("STABILIZING")
        syn_1.transition_to("INTERACTIVE")
        self.assertEqual(syn_1.get_state(), "INTERACTIVE")
        memory_1.close()
        # Simulate crash: objects are destroyed, DB file remains on disk

        # --- Recovery boot ---
        memory_2 = ELMem(self.db_path)
        recovered_state = memory_2.atomic_read("system_state")

        self.assertEqual(recovered_state, "INTERACTIVE")
        print(f"[TEST] Recovered state from DB: {recovered_state}")
        memory_2.close()

    def test_flags_survive_restart(self):
        """
        Scenario: neural_processing is activated, then system crashes.
        Expected: New instance recovers the flag value from EL_MEM.
        """
        # --- Stage 0: Boot and activate neural processing ---
        memory_1 = ELMem(self.db_path)
        syn_1 = SMSyn(memory=memory_1)
        syn_1.transition_to("STABILIZING")
        syn_1.transition_to("INTERACTIVE")
        syn_1.set_flag("neural_processing", True)
        self.assertTrue(syn_1.get_flag("neural_processing"))
        memory_1.close()

        # --- Recovery ---
        memory_2 = ELMem(self.db_path)
        recovered_flags = memory_2.atomic_read("system_flags")

        self.assertIsNotNone(recovered_flags)
        self.assertTrue(recovered_flags.get("neural_processing"))
        print(f"[TEST] Recovered flags from DB: {recovered_flags}")
        memory_2.close()

    def test_audit_trail_survives_restart(self):
        """
        Scenario: Several transitions occur, then system crashes.
        Expected: Complete audit trail is recoverable from EL_MEM.
        """
        # --- Stage 0: Boot and transitions ---
        memory_1 = ELMem(self.db_path)
        syn_1 = SMSyn(memory=memory_1)
        syn_1.transition_to("STABILIZING")
        syn_1.transition_to("INTERACTIVE")
        syn_1.transition_to("MAINTENANCE")
        memory_1.close()

        # --- Recovery — read audit trail ---
        memory_2 = ELMem(self.db_path)
        events = memory_2.read_events(limit=20)

        # Must have at least 3 transition events
        transition_events = [e for e in events if e["topic"] == "state_transition"]
        self.assertGreaterEqual(len(transition_events), 3)
        print(f"[TEST] Recovered {len(transition_events)} transition events from audit trail.")
        memory_2.close()

    def test_multiple_restarts_preserve_consistency(self):
        """
        Scenario: System restarts 3 times with different states.
        Expected: Each restart recovers the correct last known state.
        """
        states_to_persist = ["STABILIZING", "INTERACTIVE", "MAINTENANCE"]

        for expected_state in states_to_persist:
            # Write state directly (simulating a stopped system)
            memory = ELMem(self.db_path)
            memory.atomic_write("system_state", expected_state)
            memory.close()

            # Recovery read
            memory = ELMem(self.db_path)
            recovered = memory.atomic_read("system_state")
            self.assertEqual(recovered, expected_state)
            print(f"[TEST] Restart {expected_state} → recovered: {recovered} ✓")
            memory.close()


if __name__ == "__main__":
    unittest.main()
