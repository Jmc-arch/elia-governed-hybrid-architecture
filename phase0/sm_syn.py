# sm_syn.py — ELIA Phase 0
# State coordination: manages global system state and valid transitions.
# Single source of truth for system mode and flags.
#
# ARCHITECTURAL DECISION — Persistence Contract (Option B: Pessimistic)
# A state transition is only accepted if EL_MEM successfully persists it.
# If persistence fails, the transition is rolled back and returns False.
# Rationale: In a governed AI system, auditability is non-negotiable.
# An unpersisted transition is an unaudited transition — this is not allowed.

import threading
from datetime import datetime, timezone

try:
    from .el_mem import ELMem
except ImportError:  # pragma: no cover - supports direct script execution
    from el_mem import ELMem


# Valid system states
VALID_STATES = {"INIT", "STABILIZING", "INTERACTIVE", "MAINTENANCE", "SHUTDOWN"}

# Allowed state transitions
TRANSITIONS = {
    "INIT":         {"STABILIZING"},
    "STABILIZING":  {"INTERACTIVE", "SHUTDOWN"},
    "INTERACTIVE":  {"MAINTENANCE", "SHUTDOWN"},
    "MAINTENANCE":  {"INTERACTIVE", "SHUTDOWN"},
    "SHUTDOWN":     set(),  # Terminal state
}


class SMSyn:
    """
    SM_SYN — State Coordination (Phase 0 MVP)

    Persistence Contract (Option B — Pessimistic):
    - A transition is committed ONLY if EL_MEM persistence succeeds.
    - If atomic_write fails, the in-memory state is rolled back.
    - If log_event fails after a successful write, the transition is
      still committed (write succeeded = auditable) but a warning is printed.

    MVP scope: in-process only, threading.Lock, no distributed coordination.
    """

    def __init__(self, memory: ELMem):
        self._memory = memory
        self._lock = threading.Lock()
        self._state = "INIT"
        self._flags = {
            "neural_processing": False,
            "learning_enabled": False,
        }
        # Persist initial state — abort if persistence fails
        if not self._memory.atomic_write("system_state", self._state):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial state. Aborting.")
        if not self._memory.atomic_write("system_flags", self._flags):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial flags. Aborting.")
        print(f"[SM_SYN] Initialized. State: {self._state}")

    def get_state(self) -> str:
        """Return the current system state."""
        return self._state

    def transition_to(self, new_state: str) -> bool:
        """
        Attempt a state transition.

        Option B — Pessimistic persistence:
        The transition is accepted ONLY if EL_MEM successfully persists it.
        If persistence fails, the in-memory state is rolled back to previous.

        Returns True if transition succeeded and was persisted.
        Returns False if transition is invalid OR if persistence failed.
        """
        if new_state not in VALID_STATES:
            print(f"[SM_SYN] Invalid state: '{new_state}'")
            return False

        with self._lock:
            allowed = TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                print(f"[SM_SYN] Transition denied: {self._state} → {new_state}")
                return False

            previous = self._state

            # Attempt persistence BEFORE committing in-memory state
            write_ok = self._memory.atomic_write("system_state", new_state)

            if not write_ok:
                # Persistence failed — state never changed in memory (implicit rollback)
                print(
                    f"[SM_SYN] Transition ABORTED: {previous} → {new_state} "
                    f"(EL_MEM write failed — state rolled back to {previous})"
                )
                return False

            # Persistence succeeded — now commit in-memory state
            self._state = new_state

            # Log the transition event (best-effort — does not block transition)
            log_ok = self._memory.log_event(
                source="SM_SYN",
                topic="state_transition",
                payload={
                    "from": previous,
                    "to": new_state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            if not log_ok:
                print(
                    f"[SM_SYN] Warning: transition {previous} → {new_state} committed "
                    f"but event log failed. State persisted, audit trail incomplete."
                )

            print(f"[SM_SYN] Transition: {previous} → {new_state}")
            return True

    def set_flag(self, key: str, value: bool) -> bool:
        """
        Update a system flag (e.g. neural_processing).
        Option B applied: flag update only committed if EL_MEM write succeeds.
        """
        if key not in self._flags:
            print(f"[SM_SYN] Unknown flag: '{key}'")
            return False

        with self._lock:
            previous_value = self._flags[key]
            updated_flags = dict(self._flags)
            updated_flags[key] = value

            # Attempt persistence BEFORE committing in-memory
            write_ok = self._memory.atomic_write("system_flags", updated_flags)

            if not write_ok:
                print(
                    f"[SM_SYN] Flag update ABORTED: {key} = {value} "
                    f"(EL_MEM write failed — flag rolled back to {previous_value})"
                )
                return False

            # Persistence succeeded — commit in-memory
            self._flags[key] = value
            print(f"[SM_SYN] Flag updated: {key} = {value}")
            return True

    def get_flag(self, key: str) -> bool:
        """Read a system flag."""
        return self._flags.get(key, False)

    def get_system_snapshot(self) -> dict:
        """Return a full snapshot of current state and flags."""
        return {
            "state": self._state,
            "flags": self._flags.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
