"""SQLite-backed append-only execution event store.

Every state transition in the LangGraph execution is recorded as an immutable
event.  This enables full run replay, root-cause debugging, and feeds the
episodic memory system (memory.py).
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class ExecutionEvent:
    """A single immutable event emitted during graph execution."""

    run_id: str
    event_type: str  # node_entered, node_exited, route_taken, error, timeout,
    #                   plan_created, sub_goal_completed, reflection,
    #                   run_started, run_completed, run_failed
    step_number: int = 0
    node_name: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventStore:
    """Thread-safe, SQLite-backed append-only event log.

    Writes are serialised through a threading lock so the store is safe
    to use from both sync node functions and the async event loop (via
    ``asyncio.to_thread`` or direct calls from sync code).
    """

    def __init__(self, db_path: str | Path = "orchestrai_events.db") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id     TEXT    NOT NULL,
                        timestamp  REAL    NOT NULL,
                        event_type TEXT    NOT NULL,
                        node_name  TEXT,
                        step_number INTEGER DEFAULT 0,
                        payload    TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)"
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def emit(self, event: ExecutionEvent) -> None:
        """Append an event to the log."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO events (run_id, timestamp, event_type, node_name, step_number, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.run_id,
                        event.timestamp,
                        event.event_type,
                        event.node_name,
                        event.step_number,
                        json.dumps(event.payload),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Return all events for a run, ordered chronologically."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM events WHERE run_id = ? ORDER BY timestamp ASC",
                    (run_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_run_summary(self, run_id: str) -> dict[str, Any]:
        """Return aggregated stats for a run."""
        events = self.get_run_events(run_id)
        if not events:
            return {}

        node_durations: dict[str, float] = {}
        node_starts: dict[str, float] = {}
        errors: list[str] = []
        total_steps = 0

        for ev in events:
            if ev["event_type"] == "node_entered" and ev["node_name"]:
                node_starts[ev["node_name"]] = ev["timestamp"]
            elif ev["event_type"] == "node_exited" and ev["node_name"]:
                start = node_starts.pop(ev["node_name"], None)
                if start is not None:
                    dur = ev["timestamp"] - start
                    node_durations[ev["node_name"]] = (
                        node_durations.get(ev["node_name"], 0.0) + dur
                    )
            elif ev["event_type"] == "error":
                payload = json.loads(ev["payload"]) if isinstance(ev["payload"], str) else ev["payload"]
                errors.append(payload.get("message", "unknown"))
            if ev["event_type"] == "node_exited" and ev["node_name"] == "finalize_step":
                total_steps += 1

        return {
            "run_id": run_id,
            "total_events": len(events),
            "total_steps": total_steps,
            "node_durations": node_durations,
            "errors": errors,
            "first_event": events[0]["timestamp"] if events else None,
            "last_event": events[-1]["timestamp"] if events else None,
            "duration_seconds": (
                events[-1]["timestamp"] - events[0]["timestamp"]
                if len(events) >= 2
                else 0
            ),
        }

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs with basic metadata."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT run_id,
                           MIN(timestamp) AS started_at,
                           MAX(timestamp) AS ended_at,
                           COUNT(*)       AS event_count
                    FROM events
                    GROUP BY run_id
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
