# sm_log.py — ELIA Stage 1
# Unified Logging System — MUST be initialized before other modules.
#
# ARCHITECTURAL DECISIONS:
# - Storage ALWAYS via SM_SYN.log_event() (EL-ARCH rule line 821).
#   Never accesses EL_MEM directly or via private attributes.
# - All SM_SYN calls use asyncio.to_thread() — never blocks event loop.
#   (Fixes Gemini audit point 1 — asyncio vs blocking)
# - In-memory circular buffer as fallback if SM_SYN is not yet ready.
# - SM_LOG never controls operational flags — observability only.
# - receive_log_event() exposes SM_HUB integration point (EL-ARCH).

import asyncio
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .sm_syn import SMSyn


# ----------------------------------------------------------------
# Enumerations — aligned with EL-ARCH standardized interfaces
# ----------------------------------------------------------------

class LogType(str, Enum):
    NEURAL = "neural"
    SYSTEM = "system"
    PERFORMANCE = "performance"
    FEEDBACK = "feedback"
    CYCLE_INVALIDATION = "cycle_invalidation"
    ADMISSION_CONTROL = "admission_control"
    QUORUM_FAILURE = "quorum_failure"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ----------------------------------------------------------------
# SM_LOG — Unified Logging System
# ----------------------------------------------------------------

class SMLog:
    """
    SM_LOG — Unified Logging System (Stage 1 MVP)

    Responsibilities:
    - Structured logging with correlation IDs.
    - In-memory circular buffer (fallback if SM_SYN not ready).
    - Persistent storage via SM_SYN.log_event() — never EL_MEM directly.
    - Alert accumulation for SM_GSM consumption.
    - SM_HUB integration via receive_log_event().

    Storage rule (EL-ARCH line 821):
        "Write and read: ALWAYS via SM_SYN (security, consistency, atomicity)."

    Stage 1 scope:
    - JSONL structured logs persisted via SM_SYN.
    - In-memory circular buffer (max 1000 entries).
    - Log levels: debug, info, warning, error, critical.
    - Alert tracking for SM_GSM.
    - No Pydantic yet (Stage 2).
    - No psutil metrics yet (Stage 2).
    - No loguru yet (Stage 2) — _emit() uses print() as interim.

    CRITICAL: SM_LOG never controls operational flags — observability only.
    """

    BUFFER_MAX_SIZE = 1000
    SATISFACTION_ALERT_THRESHOLD = 0.4
    SATISFACTION_ALERT_CYCLES = 10

    def __init__(self, syn: "SMSyn"):
        """
        Initialize SM_LOG with SM_SYN as the storage coordinator.
        SM_SYN is injected — never EL_MEM directly (EL-ARCH rule).
        """
        self._syn = syn
        self._buffer: deque = deque(maxlen=self.BUFFER_MAX_SIZE)
        self._active_alerts: list = []
        self._satisfaction_history: deque = deque(maxlen=self.SATISFACTION_ALERT_CYCLES)
        self._lock = asyncio.Lock()
        self._emit("info", "SM_LOG initialized. Memory buffer active.")

    # ----------------------------------------------------------------
    # Console output — interim until loguru (Stage 2)
    # ----------------------------------------------------------------

    def _emit(self, level: str, message: str) -> None:
        """
        Temporary console output.
        Will be replaced by loguru with enqueue=True in Stage 2.
        """
        print(f"[SM_LOG] {level.upper()} | {message}")

    # ----------------------------------------------------------------
    # Core logging interface
    # ----------------------------------------------------------------

    async def log(
        self,
        log_type: LogType,
        source: str,
        message: str,
        level: LogLevel = LogLevel.INFO,
        data: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Record a structured log entry.
        Returns the correlation_id for traceability.

        Storage: via SM_SYN.log_event() — never directly to EL_MEM.
        Non-blocking: SM_SYN call runs in a thread pool (asyncio.to_thread).
        """
        entry = {
            "log_type": log_type.value,
            "source": source,
            "level": level.value,
            "message": message,
            "data": data or {},
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        async with self._lock:
            # Always write to in-memory buffer first (instant, never fails)
            self._buffer.append(entry)

            # Persist via SM_SYN — respects EL-ARCH rule line 821
            # asyncio.to_thread() prevents blocking the event loop
            try:
                await asyncio.to_thread(
                    self._syn.log_event,
                    source=source,
                    topic=f"log.{log_type.value}",
                    payload=entry,
                )
            except Exception as e:
                # Buffer write succeeded — persistence failure is non-fatal
                self._emit("warning", f"Failed to persist log via SM_SYN: {e}")

        self._emit(level.value, f"{source} | {message}")
        return entry["correlation_id"]

    # ----------------------------------------------------------------
    # Convenience methods
    # ----------------------------------------------------------------

    async def log_system(
        self,
        source: str,
        message: str,
        level: LogLevel = LogLevel.INFO,
        data: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        """Convenience method for system events."""
        return await self.log(
            LogType.SYSTEM, source, message, level, data, correlation_id
        )

    async def log_warning(
        self, source: str, message: str, data: Optional[dict] = None
    ) -> str:
        """Convenience method for warnings."""
        return await self.log(
            LogType.SYSTEM, source, message, LogLevel.WARNING, data
        )

    async def log_error(
        self, source: str, message: str, data: Optional[dict] = None
    ) -> str:
        """Convenience method for errors."""
        return await self.log(
            LogType.SYSTEM, source, message, LogLevel.ERROR, data
        )

    async def log_critical(
        self, source: str, message: str, data: Optional[dict] = None
    ) -> str:
        """
        Convenience method for critical events.
        Critical entries are also tracked as active alerts for SM_GSM.
        """
        alert_id = await self.log(
            LogType.SYSTEM, source, message, LogLevel.CRITICAL, data
        )
        async with self._lock:
            self._active_alerts.append({
                "alert_id": alert_id,
                "source": source,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "critical",
            })
        return alert_id

    # ----------------------------------------------------------------
    # Specialized logging methods — aligned with EL-ARCH schemas
    # ----------------------------------------------------------------

    async def log_cycle_invalidation(self, cycle_data: dict) -> str:
        """
        Records cycle invalidation (SM_OS → SM_LOG).
        Contract: cycle_data must contain cycle_id, noise_ratio, reason.
        """
        return await self.log(
            log_type=LogType.CYCLE_INVALIDATION,
            source="SM_OS",
            message=f"Cycle invalidated: {cycle_data.get('reason', 'unknown')}",
            level=LogLevel.WARNING,
            data=cycle_data,
            correlation_id=cycle_data.get("cycle_id"),
        )

    async def log_admission_event(self, admission_data: dict) -> str:
        """
        Records admission control decision (EL_IFC → SM_LOG).
        Contract: admission_data must contain request_id, accepted, reason.
        """
        level = LogLevel.INFO if admission_data.get("accepted") else LogLevel.WARNING
        return await self.log(
            log_type=LogType.ADMISSION_CONTROL,
            source="EL_IFC",
            message=f"Request {'accepted' if admission_data.get('accepted') else 'rejected'}: {admission_data.get('reason', '')}",
            level=level,
            data=admission_data,
            correlation_id=admission_data.get("request_id"),
        )

    async def log_feedback(
        self, user_id: str, value: float, context: dict
    ) -> str:
        """
        Records user feedback — passive monitoring only.
        Never influences neural activation decisions directly.
        SM_GSM may react indirectly if satisfaction alert triggers mode change.
        """
        data = {
            "user_id": user_id,
            "value": value,
            "context": context,
            "feedback_type": "explicit",
        }
        correlation_id = await self.log(
            log_type=LogType.FEEDBACK,
            source="EL_IFC",
            message=f"User feedback received: {value:.2f}",
            level=LogLevel.INFO,
            data=data,
        )
        async with self._lock:
            self._satisfaction_history.append(value)
        return correlation_id

    # ----------------------------------------------------------------
    # SM_HUB integration — EL-ARCH interface
    # ----------------------------------------------------------------

    async def receive_log_event(self, event: dict) -> None:
        """
        Entry point when log event arrives via SM_HUB.
        EL-ARCH defines this as the standard SM_HUB → SM_LOG interface.
        """
        try:
            await self.log(
                log_type=LogType(event.get("log_type", "system")),
                source=event.get("source", "unknown"),
                message=event.get("message", ""),
                level=LogLevel(event.get("level", "info")),
                data=event.get("data"),
                correlation_id=event.get("correlation_id"),
            )
        except (ValueError, KeyError) as e:
            # Invalid enum value or missing key — log as system warning
            await self.log_warning(
                source="SM_LOG",
                message=f"Invalid log event received via SM_HUB: {e}",
                data={"raw_event": event},
            )

    # ----------------------------------------------------------------
    # Alert and health interfaces — for SM_GSM consumption
    # ----------------------------------------------------------------

    def get_alert_status(self) -> list:
        """
        Returns list of active alerts.
        SM_GSM consumes this to make governance decisions.
        SM_LOG never acts on alerts directly.
        """
        return list(self._active_alerts)

    def clear_alert(self, alert_id: str) -> bool:
        """Clear a resolved alert (called by SM_GSM after handling)."""
        original_count = len(self._active_alerts)
        self._active_alerts = [
            a for a in self._active_alerts
            if a.get("alert_id") != alert_id
        ]
        return len(self._active_alerts) < original_count

    def get_satisfaction_alert_status(self) -> dict:
        """
        Checks if user satisfaction is below critical threshold.
        Returns monitoring data for SM_GSM consumption.

        Alert triggers if average < 0.4 over last 10 cycles.
        This NEVER directly modifies operational flags.
        SM_GSM decides what to do with this information.
        """
        if not self._satisfaction_history:
            return {
                "alert_active": False,
                "average_last_10": 0.5,  # Default neutral value (EL-ARCH)
                "trend": "insufficient_data",
            }

        values = list(self._satisfaction_history)
        average = sum(values) / len(values)
        alert_active = (
            len(values) >= self.SATISFACTION_ALERT_CYCLES
            and average < self.SATISFACTION_ALERT_THRESHOLD
        )

        # Trend detection
        if len(values) >= 4:
            mid = len(values) // 2
            first_half = sum(values[:mid]) / mid
            second_half = sum(values[mid:]) / (len(values) - mid)
            if second_half < first_half - 0.05:
                trend = "declining"
            elif second_half > first_half + 0.05:
                trend = "improving"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "alert_active": alert_active,
            "average_last_10": round(average, 3),
            "trend": trend,
        }

    def get_health_metrics(self) -> dict:
        """
        Returns basic health metrics.
        Full psutil integration (CPU/RAM/GPU) in Stage 2.
        """
        return {
            "buffer_size": len(self._buffer),
            "buffer_capacity": self.BUFFER_MAX_SIZE,
            "active_alerts": len(self._active_alerts),
            "satisfaction_history_size": len(self._satisfaction_history),
        }

    # ----------------------------------------------------------------
    # Query interfaces
    # ----------------------------------------------------------------

    def query_buffer(
        self,
        log_type: Optional[LogType] = None,
        level: Optional[LogLevel] = None,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """Query in-memory buffer with optional filters."""
        results = list(self._buffer)
        if log_type:
            results = [e for e in results if e.get("log_type") == log_type.value]
        if level:
            results = [e for e in results if e.get("level") == level.value]
        if source:
            results = [e for e in results if e.get("source") == source]
        return results[-limit:]

    def filter_by_correlation(self, correlation_id: str) -> list:
        """
        Search buffer by correlation_id.
        Enables full request tracing across modules.
        """
        return [
            e for e in self._buffer
            if e.get("correlation_id") == correlation_id
        ]

    def get_buffer_snapshot(self, limit: int = 100) -> list:
        """Return recent entries from the in-memory buffer."""
        return list(self._buffer)[-limit:]
