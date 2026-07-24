"""Episodic memory — learn from past task executions.

Stores trajectory summaries after each run and retrieves similar past
experiences to provide the planner with few-shot context.  Uses
``sentence-transformers`` for embedding-based similarity when available,
falling back to keyword overlap (TF-IDF style) when it is not installed.
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Embedding backend — graceful degradation
# ---------------------------------------------------------------------------

_EMBED_MODEL = None
_EMBED_AVAILABLE = False

def _get_embed_model():
    """Lazy-load the sentence-transformers model."""
    global _EMBED_MODEL, _EMBED_AVAILABLE
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBED_AVAILABLE = True
        return _EMBED_MODEL
    except ImportError:
        _EMBED_AVAILABLE = False
        return None


def _embed_text(text: str) -> bytes | None:
    """Return embedding as bytes, or None if unavailable."""
    model = _get_embed_model()
    if model is None:
        return None
    vec = model.encode(text, normalize_embeddings=True)
    return vec.astype(np.float32).tobytes()


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Compute cosine similarity between two embedding byte buffers."""
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    dot = float(np.dot(va, vb))
    return dot  # vectors are already L2-normalised


def _keyword_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity over lowercased word tokens."""
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# Episode data structure
# ---------------------------------------------------------------------------

@dataclass
class Episode:
    """A single past task execution record."""

    task_text: str
    sub_goals: list[dict[str, Any]] = field(default_factory=list)
    trajectory_summary: str = ""
    success: bool = False
    steps_taken: int = 0
    duration_seconds: float = 0.0
    failure_reason: str | None = None
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Memory store
# ---------------------------------------------------------------------------

class EpisodicMemory:
    """Thread-safe SQLite-backed episodic memory with embedding retrieval.

    Gracefully degrades to keyword similarity when
    ``sentence-transformers`` is not installed.
    """

    def __init__(self, db_path: str | Path = "orchestrai_memory.db") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS episodes (
                        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_text          TEXT NOT NULL,
                        task_embedding     BLOB,
                        sub_goals_json     TEXT,
                        trajectory_summary TEXT,
                        success            BOOLEAN,
                        steps_taken        INTEGER,
                        duration_seconds   REAL,
                        failure_reason     TEXT,
                        created_at         REAL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store_episode(self, episode: Episode) -> None:
        """Persist an episode after a run completes."""
        embedding = _embed_text(episode.task_text)
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO episodes
                        (task_text, task_embedding, sub_goals_json, trajectory_summary,
                         success, steps_taken, duration_seconds, failure_reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode.task_text,
                        embedding,
                        json.dumps(episode.sub_goals),
                        episode.trajectory_summary,
                        episode.success,
                        episode.steps_taken,
                        episode.duration_seconds,
                        episode.failure_reason,
                        episode.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def retrieve_similar(
        self, task_text: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Return the top-K most similar past episodes.

        Uses cosine similarity over sentence-transformer embeddings when
        available.  Falls back to Jaccard keyword overlap otherwise.
        """
        query_embedding = _embed_text(task_text)

        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM episodes ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            finally:
                conn.close()

        if not rows:
            return []

        scored: list[tuple[float, dict]] = []
        for row in rows:
            row_dict = dict(row)
            row_dict["sub_goals"] = (
                json.loads(row_dict["sub_goals_json"])
                if row_dict.get("sub_goals_json")
                else []
            )

            if query_embedding and row_dict.get("task_embedding"):
                score = _cosine_similarity(query_embedding, row_dict["task_embedding"])
            else:
                score = _keyword_similarity(task_text, row_dict["task_text"])

            scored.append((score, row_dict))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, ep in scored[:top_k]:
            ep["similarity_score"] = round(score, 4)
            # Remove raw embedding from response
            ep.pop("task_embedding", None)
            ep.pop("sub_goals_json", None)
            results.append(ep)

        return results

    def list_episodes(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent episodes."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, task_text, success, steps_taken, duration_seconds, created_at "
                    "FROM episodes ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def format_for_prompt(self, episodes: list[dict[str, Any]]) -> str:
        """Format retrieved episodes as context for the planner prompt."""
        if not episodes:
            return ""

        lines = ["## Similar Past Task Executions\n"]
        for i, ep in enumerate(episodes, 1):
            status = "✅ SUCCESS" if ep.get("success") else "❌ FAILED"
            lines.append(f"### Example {i} ({status}, similarity: {ep.get('similarity_score', 'N/A')})")
            lines.append(f"**Task**: {ep['task_text']}")
            if ep.get("sub_goals"):
                lines.append("**Plan used**:")
                for j, sg in enumerate(ep["sub_goals"], 1):
                    desc = sg.get("description", sg) if isinstance(sg, dict) else str(sg)
                    lines.append(f"  {j}. {desc}")
            if ep.get("trajectory_summary"):
                lines.append(f"**Summary**: {ep['trajectory_summary']}")
            if ep.get("failure_reason"):
                lines.append(f"**Failure reason**: {ep['failure_reason']}")
            lines.append(f"**Steps**: {ep.get('steps_taken', 'N/A')}\n")

        return "\n".join(lines)
