# el_mem.py — ELIA Stage 0
# Memory layer: persistent storage using SQLite.
# All state and audit trail data is written here.

import sqlite3
import json
from datetime import datetime, timezone


class ELMem:
    """
    EL_MEM — Memory Layer (Stage 0 MVP)

    Responsibilities:
    - Persist system state and events.
    - Provide atomic read/write operations.
    - Serve as the audit trail foundation.

    MVP scope: SQLite with WAL mode, minimal schema with version tracking.
    No caching, no encryption, no replication.

    ARCHITECTURAL DECISIONS:
    - WAL mode: enabled for concurrent reads during writes (EL-ARCH lines 753/784).
      Required for Stage 1+ where SM_LOG writes in parallel with other modules.
    - Schema versioning: tracks installed schema version for safe future migrations.
      New tables added in Stage 1+ must increment SCHEMA_VERSION.
    """

    # Current schema version — increment when adding or modifying tables
    SCHEMA_VERSION = 0

    def __init__(self, db_path: str = "elia.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Enable WAL mode for concurrent read/write access
        # Required by EL-ARCH spec (lines 753, 784) and Stage 1 parallel writes
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._init_schema()
        print(f"[EL_MEM] Initialized. Database: {db_path}")

    def _init_schema(self):
        """Create tables if they do not exist."""
        cursor = self._conn.cursor()

        # Schema version tracking — single source of truth for migration state
        # Increment SCHEMA_VERSION when adding or modifying tables in future stages
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT NOT NULL,
                description TEXT NOT NULL
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, 'Stage 0 — initial schema')
        """, (self.SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()))

        # Key-value store for system state flags
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Event log — append-only audit trail
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT NOT NULL,
                source     TEXT NOT NULL,
                topic      TEXT NOT NULL,
                payload    TEXT NOT NULL
            )
        """)

        self._conn.commit()
        print("[EL_MEM] Schema ready.")

    def get_schema_version(self) -> int:
        """Return the current installed schema version."""
        try:
            cursor = self._conn.execute(
                "SELECT MAX(version) as version FROM schema_version"
            )
            row = cursor.fetchone()
            return row["version"] if row and row["version"] is not None else 0
        except Exception:
            return 0

    def atomic_write(self, key: str, value) -> bool:
        """Write or update a key in the system state store."""
        try:
            serialized = json.dumps(value)
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, serialized, now),
            )
            self._conn.commit()
            print(f"[EL_MEM] Written: '{key}'")
            return True
        except Exception as e:
            print(f"[EL_MEM] Write error for '{key}': {e}")
            return False

    def atomic_read(self, key: str):
        """Read a value from the system state store. Returns None if not found."""
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

    def log_event(self, source: str, topic: str, payload: dict) -> bool:
        """Append an event to the audit log."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO event_log (timestamp, source, topic, payload) VALUES (?, ?, ?, ?)",
                (now, source, topic, json.dumps(payload)),
            )
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[EL_MEM] Log error: {e}")
            return False

    def read_events(self, limit: int = 50) -> list:
        """Read the most recent events from the audit log."""
        cursor = self._conn.execute(
            "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        """Close the database connection."""
        self._conn.close()
        print("[EL_MEM] Connection closed.")
