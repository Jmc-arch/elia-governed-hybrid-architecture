# el_mem.py — ELIA
# Memory layer: persistent storage using SQLite.
# Pure passive layer — no coordination logic, no outgoing calls.
# All access is orchestrated by SM_SYN (EL-ARCH rule, line 821).
#
# ARCHITECTURAL ROLE (EL-ARCH lines 746-767):
# - Pure passive layer: makes no business decisions, calls no other module.
# - Minimalist wrapper exposing atomic_read() and atomic_write().
# - ACID guarantees delegated to SQLite engine (not custom Python code).
# - WAL mode: concurrent reads during writes (EL-ARCH lines 753, 784).
# - Schema versioning: safe migrations across implementation stages.

import sqlite3
import json
from datetime import datetime, timezone


class ELMem:
    """
    EL_MEM — Unified Memory System (MVP scope)

    Responsibilities:
    - Persist system state and events via atomic operations.
    - Serve as the audit trail foundation.
    - Provide schema versioning for safe future migrations.

    ACID guarantees are provided by SQLite, not custom Python code.
    EL_MEM is passive: it exposes read/write primitives only.
    All coordination and locking is handled by SM_SYN.

    MVP scope:
    - SQLite with WAL mode enabled (EL-ARCH lines 753, 784).
    - Minimal schema with version tracking.
    - Simple atomic CRUD operations.
    - No caching layers (L1/L2 cache is SM_SYN responsibility).
    - No encryption, no replication.

    Schema versioning:
    - SCHEMA_VERSION = 0 : initial schema (system_state + event_log).
    - Increment SCHEMA_VERSION when adding or modifying tables.
    - All migrations must be documented in schema_version table.
    """

    # Current schema version.
    # Increment this constant AND add a migration entry when modifying tables.
    SCHEMA_VERSION: int = 0

    def __init__(self, db_path: str = "elia.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # WAL mode: allows concurrent reads during writes.
        # Required by EL-ARCH spec (lines 753, 784).
        # NORMAL sync: safe performance/durability tradeoff for WAL.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._init_schema()
        print(f"[EL_MEM] Initialized. Database: {db_path}")

    # ----------------------------------------------------------------
    # Schema initialization
    # ----------------------------------------------------------------

    def _init_schema(self) -> None:
        """
        Create tables if they do not exist.
        Insert the current schema version record on first run.
        Idempotent: safe to call on every startup.
        """
        cursor = self._conn.cursor()

        # Schema version tracking.
        # Single source of truth for migration state.
        # Future stages increment SCHEMA_VERSION and add a new INSERT here.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT NOT NULL,
                description TEXT NOT NULL
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?)
        """, (
            self.SCHEMA_VERSION,
            datetime.now(timezone.utc).isoformat(),
            "Initial schema: system_state + event_log",
        ))

        # Key-value store for system state and flags.
        # Used by SM_SYN to persist: system mode, neural_processing flag,
        # learning_enabled flag, and any other system-level key-value data.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Append-only audit trail for all system events.
        # Written by SM_SYN on behalf of all modules.
        # Never deleted — forms the immutable audit history.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source    TEXT NOT NULL,
                topic     TEXT NOT NULL,
                payload   TEXT NOT NULL
            )
        """)

        self._conn.commit()
        print("[EL_MEM] Schema ready.")

    # ----------------------------------------------------------------
    # Schema version interface
    # ----------------------------------------------------------------

    def get_schema_version(self) -> int:
        """
        Return the highest installed schema version.
        Used by SM_SYN to detect schema drift and trigger migrations.
        Returns 0 if schema_version table is empty or unreadable.
        """
        try:
            cursor = self._conn.execute(
                "SELECT MAX(version) AS version FROM schema_version"
            )
            row = cursor.fetchone()
            return row["version"] if row and row["version"] is not None else 0
        except Exception:
            return 0

    # ----------------------------------------------------------------
    # Atomic read/write interface (EL-ARCH primary interface)
    # ----------------------------------------------------------------

    def atomic_write(self, key: str, value) -> bool:
        """
        Write or update a key in the system state store.
        Uses INSERT OR UPDATE (upsert) for idempotent writes.

        Returns True on success, False on any error.
        Errors are logged to console — SM_SYN handles rollback logic.
        """
        try:
            serialized = json.dumps(value)
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (key, serialized, now),
            )
            self._conn.commit()
            print(f"[EL_MEM] Written: '{key}'")
            return True
        except Exception as e:
            print(f"[EL_MEM] Write error for '{key}': {e}")
            return False

    def atomic_read(self, key: str):
        """
        Read a value from the system state store.
        Returns the deserialized value, or None if the key does not exist.

        Never raises — errors are caught and logged.
        SM_SYN handles the None case (cache miss or missing key).
        """
        try:
            cursor = self._conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["value"])
            return None
        except Exception as e:
            print(f"[EL_MEM] Read error for '{key}': {e}")
            return None

    # ----------------------------------------------------------------
    # Audit trail interface
    # ----------------------------------------------------------------

    def log_event(self, source: str, topic: str, payload: dict) -> bool:
        """
        Append an event to the immutable audit trail.
        Called exclusively by SM_SYN on behalf of all modules.

        Returns True on success, False on any error.
        The audit trail is append-only: no update or delete operations.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO event_log (timestamp, source, topic, payload) "
                "VALUES (?, ?, ?, ?)",
                (now, source, topic, json.dumps(payload)),
            )
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[EL_MEM] Log error: {e}")
            return False

    def read_events(self, limit: int = 50) -> list:
        """
        Read the most recent events from the audit trail.
        Results are ordered newest-first.

        Used by SM_SYN for state recovery and by SM_LOG for audit queries.
        """
        try:
            cursor = self._conn.execute(
                "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"[EL_MEM] Read events error: {e}")
            return []

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    def close(self) -> None:
        """
        Close the database connection.
        Must be called during system shutdown after SM_SYN has released all locks.
        """
        self._conn.close()
        print("[EL_MEM] Connection closed.")
