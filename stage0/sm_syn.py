# sm_syn.py — ELIA Stage 0 / Stage 1 ready
# State coordination + unique entry point to EL_MEM.
#
# ARCHITECTURAL DECISIONS:
# - Option B (Pessimistic) persistence: transition only if EL_MEM write succeeds.
# - EL-ARCH rule line 821: "Write and read: ALWAYS via SM_SYN."
#   Public log_event() exposes SM_SYN as the unique orchestrator for storage.
# - Logging abstraction (_emit): print() in Stage 0, SM_LOG in Stage 1+.
#   Inject SM_LOG via set_logger() — no code change needed in SM_SYN.
#
# DEADLOCK PREVENTION:
# - threading.Lock() is NOT reentrant.
# - log_event() is intentionally lock-free: it is called from within
#   transition_to() and set_flag() which already hold self._lock.
#   Acquiring the lock again inside log_event() would cause a deadlock.
# - log_event() called externally (e.g. by SM_LOG) is also safe:
#   it only writes to EL_MEM, which has its own internal ACID guarantees.

import threading
from datetime import datetime, timezone
from typing import Callable, Optional

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
    SM_SYN — Synchronization & State Coordination (Stage 0 / Stage 1 ready)

    Unique orchestrator for EL_MEM access (EL-ARCH line 821).

    Persistence Contract (Option B — Pessimistic):
    - A transition is committed ONLY if EL_MEM persistence succeeds.
    - If atomic_write fails, the in-memory state is rolled back.
    - log_event() after a successful write is best-effort and does not
      block or roll back the transition if it fails.

    Logging abstraction (_emit):
    - Stage 0: _emit() uses print() — no dependency on SM_LOG.
    - Stage 1: call set_logger(sm_log.log_system) after SM_LOG is initialized.
      All _emit() calls are then routed to SM_LOG automatically.

    Lock model:
    - self._lock is a standard (non-reentrant) threading.Lock.
    - log_event() is intentionally lock-free to prevent deadlocks
      when called from within transition_to() or set_flag().
    """

    def __init__(self, memory: ELMem):
        self._memory = memory
        self._lock = threading.Lock()
        self._state = "INIT"
        self._flags = {
            "neural_processing": False,
            "learning_enabled": False,
        }
        # Logger — None in Stage 0 (print), injected via set_logger() in Stage 1
        self._logger: Optional[Callable] = None

        # Persist initial state — abort if persistence fails (Option B)
        if not self._memory.atomic_write("system_state", self._state):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial state. Aborting.")
        if not self._memory.atomic_write("system_flags", self._flags):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial flags. Aborting.")
        self._emit("info", f"Initialized. State: {self._state}")

    # ----------------------------------------------------------------
    # Logging abstraction — Stage 0 / Stage 1 bridge
    # ----------------------------------------------------------------

    def set_logger(self, logger: Callable) -> None:
        """
        Inject SM_LOG after initialization (Stage 1+).
        Once set, all _emit() calls are routed to SM_LOG.

        Call this after SM_LOG is initialized, before system goes INTERACTIVE.

        Usage (Stage 1):
            syn.set_logger(sm_log.log_system)
        """
        self._logger = logger
        self._emit("info", "Logger injected — SM_LOG active for SM_SYN.")

    def _emit(self, level: str, message: str, data: Optional[dict] = None) -> None:
        """
        Internal logging abstraction. Never raises.
        - Stage 0: prints to console.
        - Stage 1+: routes to SM_LOG via injected logger.
        """
        try:
            if self._logger is not None:
                self._logger(
                    source="SM_SYN",
                    message=message,
                    data=data or {},
                )
            else:
                print(f"[SM_SYN] {level.upper()} | {message}")
        except Exception:
            # Logging must NEVER crash SM_SYN — silent fallback
            print(f"[SM_SYN] {message}")

    # ----------------------------------------------------------------
    # Public storage interface — EL-ARCH rule line 821
    # ----------------------------------------------------------------

    def log_event(self, source: str, topic: str, payload: dict) -> bool:
        """
        Public entry point for event persistence via SM_SYN.
        Used by SM_LOG and other modules — respects EL-ARCH rule:
        "Write and read: ALWAYS via SM_SYN (security, consistency, atomicity)."

        INTENTIONALLY LOCK-FREE:
        This method is called both externally (SM_LOG) and internally
        from within transition_to() / set_flag() which already hold self._lock.
        Acquiring the lock here would cause a deadlock with threading.Lock.
        EL_MEM provides its own ACID guarantees via SQLite WAL.

        Returns True if persistence succeeded, False otherwise.
        """
        try:
            success = self._memory.log_event(
                source=source,
                topic=topic,
                payload=payload,
            )
            if not success:
                self._emit("warning", f"log_event failed for topic: {topic}")
            return success
        except Exception as e:
            self._emit("error", f"Exception in log_event: {e}", {"topic": topic})
            return False

    # ----------------------------------------------------------------
    # State management
    # ----------------------------------------------------------------

    def get_state(self) -> str:
        """Return the current system state."""
        return self._state

    def transition_to(self, new_state: str) -> bool:
        """
        Attempt a state transition (Option B — Pessimistic).

        Returns True if transition succeeded and was persisted.
        Returns False if transition is invalid OR persistence failed.
        """
        if new_state not in VALID_STATES:
            self._emit("warning", f"Invalid state: '{new_state}'")
            return False

        with self._lock:
            allowed = TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                self._emit("warning", f"Transition denied: {self._state} → {new_state}")
                return False

            previous = self._state

            # Attempt persistence BEFORE committing in-memory state
            write_ok = self._memory.atomic_write("system_state", new_state)

            if not write_ok:
                self._emit(
                    "error",
                    f"Transition ABORTED: {previous} → {new_state} "
                    f"(EL_MEM write failed — state rolled back to {previous})",
                )
                return False

            # Persistence succeeded — commit in-memory state
            self._state = new_state

            # Best-effort event logging — lock-free (see log_event() docstring)
            self.log_event(
                source="SM_SYN",
                topic="state_transition",
                payload={
                    "from": previous,
                    "to": new_state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            self._emit("info", f"Transition: {previous} → {new_state}")
            return True

    # ----------------------------------------------------------------
    # Flag management
    # ----------------------------------------------------------------

    def set_flag(self, key: str, value: bool) -> bool:
        """
        Update a system flag (e.g. neural_processing).
        Option B applied: flag update only committed if EL_MEM write succeeds.
        """
        if key not in self._flags:
            self._emit("warning", f"Unknown flag: '{key}'")
            return False

        with self._lock:
            previous_value = self._flags[key]
            updated_flags = dict(self._flags)
            updated_flags[key] = value

            # Attempt persistence BEFORE committing in-memory
            write_ok = self._memory.atomic_write("system_flags", updated_flags)

            if not write_ok:
                self._emit(
                    "error",
                    f"Flag update ABORTED: {key} = {value} "
                    f"(EL_MEM write failed — flag rolled back to {previous_value})",
                )
                return False

            # Persistence succeeded — commit in-memory
            self._flags[key] = value
            self._emit("info", f"Flag updated: {key} = {value}")
            return True

    def get_flag(self, key: str) -> bool:
        """Read a system flag."""
        return self._flags.get(key, False)

    # ----------------------------------------------------------------
    # Snapshot
    # ----------------------------------------------------------------

    def get_system_snapshot(self) -> dict:
        """Return a full snapshot of current state and flags."""
        return {
            "state": self._state,
            "flags": self._flags.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
