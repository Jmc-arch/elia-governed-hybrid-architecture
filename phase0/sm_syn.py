# sm_syn.py — ELIA Phase 0
# State coordination: manages global system state and valid transitions.
# Single source of truth for system mode and flags.

import threading
from datetime import datetime
from el_mem import ELMem


# Valid system states
VALID_STATES = {"INIT", "STABILIZING", "INTERACTIVE", "MAINTENANCE", "SHUTDOWN"}

# Allowed state transitions
TRANSITIONS = {
    "INIT":         {"STABILIZING"},
    "STABILIZING":  {"INTERACTIVE", "SHUTDOWN"},
    "INTERACTIVE":  {"MAINTENANCE", "SHUTDOWN"},
    "MAINTENANCE":  {"INTERACTIVE", "SHUTDOWN"},
    "SHUTDOWN":     set()  # Terminal state
}


class SMSyn:
    """
    SM_SYN — State Coordination (Phase 0 MVP)

    Responsibilities:
    - Track current system state.
    - Enforce valid state transitions.
    - Manage the neural_processing flag.
    - Provide basic locking for atomic operations.

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
        # Persist initial state
        self._memory.atomic_write("system_state", self._state)
        self._memory.atomic_write("system_flags", self._flags)
        print(f"[SM_SYN] Initialized. State: {self._state}")

    def get_state(self) -> str:
        """Return the current system state."""
        return self._state

    def transition_to(self, new_state: str) -> bool:
        """
        Attempt a state transition.
        Returns True if successful, False if transition is not allowed.
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
            self._state = new_state
            self._memory.atomic_write("system_state", self._state)
            self._memory.log_event(
                source="SM_SYN",
                topic="state_transition",
                payload={"from": previous, "to": new_state,
                         "timestamp": datetime.utcnow().isoformat()}
            )
            print(f"[SM_SYN] Transition: {previous} → {new_state}")
            return True

    def set_flag(self, key: str, value: bool) -> bool:
        """Update a system flag (e.g. neural_processing)."""
        if key not in self._flags:
            print(f"[SM_SYN] Unknown flag: '{key}'")
            return False
        with self._lock:
            self._flags[key] = value
            self._memory.atomic_write("system_flags", self._flags)
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
            "timestamp": datetime.utcnow().isoformat()
        }
