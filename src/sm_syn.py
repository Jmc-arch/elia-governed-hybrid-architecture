# sm_syn.py — ELIA
# State coordination and unique entry point to EL_MEM.
# All EL_MEM access is orchestrated exclusively through SM_SYN.
#
# ARCHITECTURAL ROLE (EL-ARCH lines 165-279):
# - Unique entry point to EL_MEM (EL-ARCH line 821: "ALWAYS via SM_SYN").
# - Manages global system state and valid transitions.
# - Manages coordination locks (Coordination Zone).
# - Provides logging abstraction for Stage 0 → Stage 1 transition.
#
# PERSISTENCE CONTRACT — Option B (Pessimistic):
# A state transition or flag update is committed ONLY if EL_MEM
# persistence succeeds. If atomic_write fails, the in-memory state
# is rolled back. Rationale: an unpersisted state is an unaudited
# state — not allowed in a governed AI system.
#
# DEADLOCK PREVENTION:
# log_event() calls inside transition_to() and set_flag() bypass
# the public log_event() method and call _memory.log_event() directly
# because the coordination lock is already held at that point.
# The public log_event() method acquires the lock — calling it while
# the lock is held would cause a deadlock (threading.Lock is not reentrant).

import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from el_mem import ELMem


# ----------------------------------------------------------------
# Valid states and allowed transitions — EL-ARCH runtime cycle
# ----------------------------------------------------------------

VALID_STATES = {
    "INIT",
    "STABILIZING",
    "INTERACTIVE",
    "MAINTENANCE",
    "SHUTDOWN",
}

# Allowed transitions — enforces the state machine contract.
# Any transition not listed here is denied and logged.
TRANSITIONS: dict = {
    "INIT":        {"STABILIZING"},
    "STABILIZING": {"INTERACTIVE", "SHUTDOWN"},
    "INTERACTIVE": {"MAINTENANCE", "SHUTDOWN"},
    "MAINTENANCE": {"INTERACTIVE", "SHUTDOWN"},
    "SHUTDOWN":    set(),  # Terminal state — no outgoing transitions
}

# System flags — governance invariants
# neural_processing: False by default — neural must NEVER auto-activate.
# learning_enabled:  False by default — learning requires explicit authorization.
DEFAULT_FLAGS: dict = {
    "neural_processing": False,
    "learning_enabled": False,
}


# ----------------------------------------------------------------
# SM_SYN — State Coordination Module
# ----------------------------------------------------------------

class SMSyn:
    """
    SM_SYN — Synchronization and State Coordination (MVP scope)

    Responsibilities:
    - Unique entry point to EL_MEM for all modules.
    - Global system state tracking with explicit transition enforcement.
    - Governance flag management (neural_processing, learning_enabled).
    - Coordination Zone lock for multi-step atomic operations.
    - Logging abstraction: console in MVP, SM_LOG after injection.

    Persistence contract (Option B — Pessimistic):
    - Transitions and flag updates are committed ONLY if EL_MEM write succeeds.
    - If persistence fails, in-memory state is rolled back.
    - Rationale: auditability is non-negotiable in a governed AI system.

    Logging abstraction:
    - MVP: _emit() writes to console (no SM_LOG dependency at boot).
    - Stage 1+: call set_logger(sm_log.log_system) after SM_LOG is ready.
      All _emit() calls are automatically routed to SM_LOG.
      No code change required in SM_SYN for the transition.

    Storage interface:
    - log_event() is a public method for SM_LOG to persist entries via SM_SYN.
    - Respects EL-ARCH rule line 821: "Write and read: ALWAYS via SM_SYN."

    MVP scope:
    - In-process threading.Lock (Coordination Zone).
    - No distributed coordination.
    - No L1 cache (future stage).
    - No backup/restore (future stage).
    """

    def __init__(self, memory: ELMem):
        self._memory = memory
        self._lock = threading.Lock()  # Coordination Zone lock
        self._state = "INIT"
        self._flags = dict(DEFAULT_FLAGS)

        # Logger: None in MVP (uses print), injected in Stage 1+
        self._logger: Optional[Callable] = None

        # Persist initial state — abort system if persistence fails.
        # A system that cannot persist its initial state cannot be governed.
        if not self._memory.atomic_write("system_state", self._state):
            raise RuntimeError(
                "[SM_SYN] CRITICAL: Cannot persist initial state. Aborting."
            )
        if not self._memory.atomic_write("system_flags", self._flags):
            raise RuntimeError(
                "[SM_SYN] CRITICAL: Cannot persist initial flags. Aborting."
            )

        self._emit("info", "SM_SYN initialized.")

    # ----------------------------------------------------------------
    # Logging abstraction — MVP / Stage 1+ bridge
    # ----------------------------------------------------------------

    def set_logger(self, logger: Callable) -> None:
        """
        Inject SM_LOG after initialization (Stage 1+).
        Once set, all _emit() calls are routed to SM_LOG automatically.
        Call this after SM_LOG is ready, before system enters INTERACTIVE.

        Usage:
            syn.set_logger(sm_log.log_system)
        """
        self._logger = logger
        self._emit("info", "Logger injected — SM_LOG active.")

    def _emit(self, level: str, message: str, data: Optional[dict] = None) -> None:
        """
        Internal logging abstraction. Never raises.
        MVP: prints to console.
        Stage 1+: routes to SM_LOG via injected logger.
        Falls back to print if logger raises.
        """
        try:
            if self._logger is not None:
                self._logger(
                    source="SM_SYN",
                    message=message,
                    level=level,
                    data=data or {},
                )
            else:
                print(f"[SM_SYN] {level.upper()} | {message}")
        except Exception:
            # Logging must never crash SM_SYN
            print(f"[SM_SYN] {message}")

    # ----------------------------------------------------------------
    # Public storage interface — EL-ARCH rule line 821
    # ----------------------------------------------------------------

    def log_event(self, source: str, topic: str, payload: dict) -> bool:
        """
        Public entry point for event persistence via SM_SYN.
        Used by SM_LOG and future modules to store log entries.

        Acquires Coordination Zone lock before writing.
        Respects EL-ARCH rule: "Write and read: ALWAYS via SM_SYN."

        Do NOT call this method while already holding the lock.
        Internal methods use _memory.log_event() directly to avoid deadlock.
        """
        with self._lock:
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
                self._emit("error", f"log_event exception: {e}", {"topic": topic})
                return False

    # ----------------------------------------------------------------
    # State management
    # ----------------------------------------------------------------

    def get_state(self) -> str:
        """Return the current system state."""
        return self._state

    def transition_to(self, new_state: str) -> bool:
        """
        Attempt a state transition.

        Option B — Pessimistic persistence:
        The transition is committed ONLY if EL_MEM persistence succeeds.
        If atomic_write fails, in-memory state is rolled back to previous.

        Returns True if transition succeeded and was persisted.
        Returns False if transition is invalid or persistence failed.
        """
        if new_state not in VALID_STATES:
            self._emit("warning", f"Invalid state: '{new_state}'")
            return False

        with self._lock:
            allowed = TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                self._emit(
                    "warning",
                    f"Transition denied: {self._state} → {new_state}",
                )
                return False

            previous = self._state

            # Attempt persistence BEFORE committing in-memory state.
            # If write fails, state never changes — implicit rollback.
            write_ok = self._memory.atomic_write("system_state", new_state)
            if not write_ok:
                self._emit(
                    "error",
                    f"Transition aborted: {previous} → {new_state} "
                    f"(EL_MEM write failed — rolled back to {previous})",
                )
                return False

            # Persistence succeeded — commit in-memory state.
            self._state = new_state

            # Audit trail — best-effort, does not block the transition.
            # Direct _memory call: lock is already held here.
            try:
                self._memory.log_event(
                    source="SM_SYN",
                    topic="state_transition",
                    payload={
                        "from": previous,
                        "to": new_state,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass  # Audit trail failure does not roll back the transition

            self._emit("info", f"Transition: {previous} → {new_state}")
            return True

    # ----------------------------------------------------------------
    # Flag management
    # ----------------------------------------------------------------

    def set_flag(self, key: str, value: bool) -> bool:
        """
        Update a governance flag.
        Option B applied: flag update committed ONLY if EL_MEM write succeeds.

        Governance invariant: neural_processing is False by default.
        It can only be set to True by SM_SGA after explicit eligibility check.
        """
        if key not in self._flags:
            self._emit("warning", f"Unknown flag: '{key}'")
            return False

        with self._lock:
            previous = self._flags[key]
            updated = dict(self._flags)
            updated[key] = value

            # Attempt persistence BEFORE committing in-memory.
            write_ok = self._memory.atomic_write("system_flags", updated)
            if not write_ok:
                self._emit(
                    "error",
                    f"Flag update aborted: {key} = {value} "
                    f"(EL_MEM write failed — rolled back to {previous})",
                )
                return False

            # Persistence succeeded — commit in-memory.
            self._flags[key] = value

            # Audit trail — best-effort, direct _memory call (lock held).
            try:
                self._memory.log_event(
                    source="SM_SYN",
                    topic="flag_update",
                    payload={
                        "key": key,
                        "value": value,
                        "previous": previous,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass

            self._emit("info", f"Flag updated: {key} = {value}")
            return True

    def get_flag(self, key: str) -> bool:
        """Read a governance flag. Returns False for unknown keys."""
        return self._flags.get(key, False)

    # ----------------------------------------------------------------
    # System snapshot
    # ----------------------------------------------------------------

    def get_system_snapshot(self) -> dict:
        """
        Return a consistent snapshot of current state and flags.
        Used by SM_LOG and monitoring tools for observability.
        """
        return {
            "state": self._state,
            "flags": self._flags.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
