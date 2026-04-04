# sm_log.py — ELIA Stage 1
# Unified Logging System — MUST be initialized before other modules

import asyncio
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .sm_syn import SMSyn

# ----------------------------------------------------------------
# Enums (aligned with EL-ARCH)
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


class SMLog:
    """
    SM_LOG — Unified Logging System (Stage 1 MVP)
    - Storage ALWAYS via SM_SYN (EL-ARCH rule)
    - All writes non-blocking via asyncio.to_thread()
    - Observability only — never controls flags
    """

    BUFFER_MAX_SIZE = 1000
    SATISFACTION_ALERT_THRESHOLD = 0.4
    SATISFACTION_ALERT_CYCLES = 10

    def __init__(self, syn: "SMSyn"):
        self._syn = syn
        self._buffer: deque = deque(maxlen=self.BUFFER_MAX_SIZE)
        self._active_alerts: list = []
        self._satisfaction_history: deque = deque(maxlen=self.SATISFACTION_ALERT_CYCLES)
        self._lock = asyncio.Lock()

        self._emit("info", "SM_LOG initialized with SM_SYN storage backend.")

    def _emit(self, level: str, message: str):
        """Temporary console output — will be replaced by loguru later."""
        print(f"[SM_LOG] {level.upper()} | {message}")

    # ----------------------------------------------------------------
    # Core logging (non-blocking)
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
            self._buffer.append(entry)

            try:
                await asyncio.to_thread(
                    self._syn.log_event,
                    source=source,
                    topic=f"log.{log_type.value}",
                    payload=entry,
                )
            except Exception as e:
                self._emit("warning", f"Failed to persist log via SM_SYN: {e}")

        self._emit(level.value, f"{source} | {message}")
        return entry["correlation_id"]

    # Convenience methods
    async def log_system(self, source: str, message: str, level: LogLevel = LogLevel.INFO, data: Optional[dict] = None):
        return await self.log(LogType.SYSTEM, source, message, level, data)

    async def log_warning(self, source: str, message: str, data: Optional[dict] = None):
        return await self.log(LogType.SYSTEM, source, message, LogLevel.WARNING, data)

    async def log_error(self, source: str, message: str, data: Optional[dict] = None):
        return await self.log(LogType.SYSTEM, source, message, LogLevel.ERROR, data)

    async def log_critical(self, source: str, message: str, data: Optional[dict] = None):
        alert_id = await self.log(LogType.SYSTEM, source, message, LogLevel.CRITICAL, data)
        async with self._lock:
            self._active_alerts.append({
                "alert_id": alert_id,
                "source": source,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "critical",
            })
        return alert_id

    # Specialized methods (aligned with EL-ARCH)
    async def log_cycle_invalidation(self, cycle_data: dict) -> str:
        return await self.log(
            LogType.CYCLE_INVALIDATION,
            "SM_OS",
            f"Cycle invalidated: {cycle_data.get('reason', 'unknown')}",
            LogLevel.WARNING,
            cycle_data,
            cycle_data.get("cycle_id")
        )

    async def log_admission_event(self, admission_data: dict) -> str:
        level = LogLevel.INFO if admission_data.get("accepted") else LogLevel.WARNING
        return await self.log(
            LogType.ADMISSION_CONTROL,
            "EL_IFC",
            f"Request {'accepted' if admission_data.get('accepted') else 'rejected'}",
            level,
            admission_data,
            admission_data.get("request_id")
        )

    async def log_feedback(self, user_id: str, value: float, context: dict) -> str:
        data = {"user_id": user_id, "value": value, "context": context, "feedback_type": "explicit"}
        cid = await self.log(LogType.FEEDBACK, "EL_IFC", f"User feedback: {value:.2f}", LogLevel.INFO, data)
        async with self._lock:
            self._satisfaction_history.append(value)
        return cid

    # ----------------------------------------------------------------
    # SM_HUB interface (required by EL-ARCH)
    # ----------------------------------------------------------------
    async def receive_log_event(self, event: dict):
        """Entry point when message comes from SM_HUB."""
        try:
            await self.log(
                log_type=LogType(event.get("log_type", "system")),
                source=event.get("source", "unknown"),
                message=event.get("message", ""),
                level=LogLevel(event.get("level", "info")),
                data=event.get("data"),
                correlation_id=event.get("correlation_id")
            )
        except Exception as e:
            self._emit("warning", f"Invalid event from SM_HUB: {e}")

    # ----------------------------------------------------------------
    # Alert & Health interfaces for SM_GSM
    # ----------------------------------------------------------------
    def get_alert_status(self) -> list:
        return list(self._active_alerts)

    def clear_alert(self, alert_id: str) -> bool:
        original = len(self._active_alerts)
        self._active_alerts = [a for a in self._active_alerts if a.get("alert_id") != alert_id]
        return len(self._active_alerts) < original

    def get_satisfaction_alert_status(self) -> dict:
        if not self._satisfaction_history:
            return {"alert_active": False, "average_last_10": 0.5, "trend": "insufficient_data"}

        values = list(self._satisfaction_history)
        avg = sum(values) / len(values)
        alert_active = len(values) >= self.SATISFACTION_ALERT_CYCLES and avg < self.SATISFACTION_ALERT_THRESHOLD

        trend = "insufficient_data"
        if len(values) >= 4:
            mid = len(values) // 2
            first = sum(values[:mid]) / mid
            second = sum(values[mid:]) / (len(values) - mid)
            trend = "declining" if second < first - 0.05 else "improving" if second > first + 0.05 else "stable"

        return {
            "alert_active": alert_active,
            "average_last_10": round(avg, 3),
            "trend": trend,
        }

    def get_health_metrics(self) -> dict:
        return {
            "buffer_size": len(self._buffer),
            "buffer_capacity": self.BUFFER_MAX_SIZE,
            "active_alerts": len(self._active_alerts),
            "satisfaction_history_size": len(self._satisfaction_history),
        }
