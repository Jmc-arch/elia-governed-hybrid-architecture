# sm_log.py — ELIA Stage 1
# Unified logging system: structured logging, correlation, audit trail.
# First module initialized in Stage 1 — must be active before all others.
#
# ARCHITECTURAL DECISIONS:
# - Storage ALWAYS via SM_SYN (EL-ARCH rule, line 821: "Write and read: ALWAYS via SM_SYN").
#   SM_SYN is the unique entry point to EL_MEM — never bypass it.
#   (Corrected after Grok audit — previous version violated this rule)
# - All SM_SYN calls use asyncio.to_thread() to avoid blocking the event loop.
#   (Fixes Gemini audit point 1 — asyncio vs blocking)
# - In-memory circular buffer as fallback if SM_SYN is not yet ready.
# - Correlation IDs propagated on every log entry for full auditability.
# - SM_LOG never controls operational flags — observability only.

import asyncio
import json
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
# Log entry
# ----------------------------------------------------------------

class LogEntry:
    """Standard log entry format for all SM_LOG records."""

    def __init__(
        self,
        log_type: LogType,
        source: str,
        level: LogLevel,
        message: str,
        data: dict,
        correlation_id: Optional[str] = None,
    ):
        self.log_type = log_type
        self.source = source
        self.level = level
        self.message = message
        self.data = data
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "log_type": self.log_type.value,
            "source": self.source,
            "level": self.level.value,
            "message": self.message,
            "data": self.data,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict())


# ----------------------------------------------------------------
# SM_LOG — Unified Logging System
# ----------------------------------------------------------------

class SMLog:
    """
    SM_LOG — Unified Logging System (Stage 1 MVP)

    Responsibilities:
    - Structured logging with correlation IDs.
    - In-memory circular buffer (fallback if SM_SYN not ready).
    - Persistent storage via SM_SYN (the ONLY entry point to EL_MEM).
    - Basic alert tracking for SM_GSM consumption.

    Storage rule (EL-ARCH line 821):
        "Write and read: ALWAYS via SM_SYN (security, consistency, atomicity)."
    SM_LOG never calls EL_MEM directly.

    Stage 1 scope:
    - JSONL structured logs persisted via SM_SYN.
    - In-memory circular buffer (max 1000 entries).
    - Log levels: debug, info, warning, error, critical.
    - Alert accumulation for SM_GSM consumption.
    - No pattern analysis yet (Stage 2).
    - No psutil metrics yet (Stage 2).

    CRITICAL: All SM_SYN calls are non-blocking (asyncio.to_thread).
    SM_LOG never controls operational flags — observability only.
    """

    # In-memory buffer capacity
    BUFFER_MAX_SIZE = 1000

    # Alert thresholds (EL-ARCH)
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
        print("[SM_LOG] Initialized. Memory buffer active.")

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
        Non-blocking: SM_SYN call runs in a thread pool.
        """
        entry = LogEntry(
            log_type=log_type,
            source=source,
            level=level,
            message=message,
            data=data or {},
            correlation_id=correlation_id,
        )

        async with self._lock:
            # Always write to in-memory buffer first (instant, never fails)
            self._buffer.append(entry.to_dict())

            # Persist via SM_SYN — respects EL-ARCH rule line 821
            # asyncio.to_thread() prevents blocking the event loop (Gemini fix)
            await asyncio.to_thread(
                self._syn._memory.log_event,
                source=source,
                topic=f"log.{log_type.value}",
                payload=entry.to_dict(),
            )

        # Console output for Stage 1 visibility
        # Will be replaced by admin dashboard in Stage 2
        level_prefix = {
            LogLevel.DEBUG:    "[DEBUG]",
            LogLevel.INFO:     "[INFO ]",
            LogLevel.WARNING:  "[WARN ]",
            LogLevel.ERROR:    "[ERROR]",
            LogLevel.CRITICAL: "[CRIT ]",
        }.get(level, "[INFO ]")
        print(f"[SM_LOG] {level_prefix} {source} | {message}")

        return entry.correlation_id

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
