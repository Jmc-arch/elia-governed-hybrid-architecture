# sm_syn.py — ELIA Stage 1
# State coordination + unique entry point to EL_MEM
# ARCHITECTURAL DECISIONS:
# - ALWAYS via SM_SYN rule (EL-ARCH line 821)
# - Option B (Pessimistic) persistence
# - Coordination Zone lock (threading.Lock for Stage 1)
# - Public log_event() for SM_LOG and future modules

import threading
from datetime import datetime, timezone
from typing import Callable, Optional

try:
    from stage0.el_mem import ELMem
except ImportError:  
    from stage0.el_mem import ELMem


VALID_STATES = {"INIT", "STABILIZING", "INTERACTIVE", "MAINTENANCE", "SHUTDOWN"}

TRANSITIONS = {
    "INIT":         {"STABILIZING"},
    "STABILIZING":  {"INTERACTIVE", "SHUTDOWN"},
    "INTERACTIVE":  {"MAINTENANCE", "SHUTDOWN"},
    "MAINTENANCE":  {"INTERACTIVE", "SHUTDOWN"},
    "SHUTDOWN":     set(),
}


class SMSyn:
    """
    SM_SYN — Synchronization & State Coordination Module (Stage 1)
    Unique orchestrator for EL_MEM access.
    """

    def __init__(self, memory: ELMem):
        self._memory = memory
        self._lock = threading.Lock()                    # Coordination Zone lock (Stage 1)
        self._state = "INIT"
        self._flags = {
            "neural_processing": False,
            "learning_enabled": False,
        }
        self._logger: Optional[Callable] = None

        # Persist initial state (Option B - Pessimistic)
        if not self._memory.atomic_write("system_state", self._state):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial state. Aborting.")

        if not self._memory.atomic_write("system_flags", self._flags):
            raise RuntimeError("[SM_SYN] CRITICAL: Cannot persist initial flags. Aborting.")

        self._emit("info", "SM_SYN initialized.")

    def set_logger(self, logger: Callable) -> None:
        """Inject SM_LOG after initialization (Stage 1)."""
        self._logger = logger
        self._emit("info", "Logger injected — routing to SM_LOG active.")

    def _emit(self, level: str, message: str, data: Optional[dict] = None) -> None:
        """Never fails. Stage 0 = print, Stage 1 = SM_LOG."""
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
            print(f"[SM_SYN] {message}")

    def log_event(self, source: str, topic: str, payload: dict) -> bool:
        """Public method used by SM_LOG (and future modules)."""
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
                self._emit("error", f"Exception in log_event: {e}", {"topic": topic})
                return False

    def get_state(self) -> str:
        return self._state

    def transition_to(self, new_state: str) -> bool:
        if new_state not in VALID_STATES:
            self._emit("warning", f"Invalid state: {new_state}")
            return False

        with self._lock:
            allowed = TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                self._emit("warning", f"Transition denied: {self._state} → {new_state}")
                return False

            previous = self._state
            write_ok = self._memory.atomic_write("system_state", new_state)

            if not write_ok:
                self._emit("error", f"Transition aborted - EL_MEM write failed: {previous} → {new_state}")
                return False

            self._state = new_state

            # FIX DEADLOCK: direct call to _memory (we already hold the lock)
            try:
                self._memory.log_event(
                    source="SM_SYN",
                    topic="state_transition",
                    payload={
                        "from": previous,
                        "to": new_state,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                pass  # best-effort

            self._emit("info", f"State transition: {previous} → {new_state}")
            return True

    def set_flag(self, key: str, value: bool) -> bool:
        if key not in self._flags:
            self._emit("warning", f"Unknown flag: {key}")
            return False

        with self._lock:
            previous = self._flags[key]
            updated = dict(self._flags)
            updated[key] = value

            if not self._memory.atomic_write("system_flags", updated):
                self._emit("error", f"Flag update aborted: {key} = {value}")
                return False

            self._flags[key] = value

            # FIX DEADLOCK: direct call to _memory (we already hold the lock)
            try:
                self._memory.log_event(
                    source="SM_SYN",
                    topic="flag_update",
                    payload={"key": key, "value": value}
                )
            except Exception:
                pass

            self._emit("info", f"Flag updated: {key} = {value}")
            return True

    def get_flag(self, key: str) -> bool:
        return self._flags.get(key, False)

    def get_system_snapshot(self) -> dict:
        return {
            "state": self._state,
            "flags": self._flags.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
