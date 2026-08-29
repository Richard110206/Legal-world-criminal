"""Persistent scoring task queue for teaching pipeline reliability.

Replaces fire-and-forget daemon threads: scoring jobs are persisted in a
SQLite table so they survive process restarts, run on a bounded thread pool
(LLM concurrency limit), and failed jobs are retried up to a configured
attempt count.

Lifecycle:
    pending -> running -> done
                       -> failed (attempts < max -> back to pending)

Idempotency: the natural key is (case_id, stage, student_id). A job that is
already pending/running is not re-enqueued; a completed/failed job submitted
again is treated as an explicit re-score request.

The queue is lazy-initialised on first submit: stale `running` rows (crash
leftovers) are reset to `pending` and workers are started. No application
lifecycle wiring is required.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[2] / "sandbox_data" / "teaching" / "scoring_tasks.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scoring_tasks (
    task_key        TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    stage           TEXT NOT NULL,
    student_id      TEXT NOT NULL DEFAULT '',
    case_output_dir TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scoring_tasks_status ON scoring_tasks (status);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ScoringTaskQueue:
    """SQLite-backed scoring job queue with bounded worker pool."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        max_workers: int | None = None,
        max_attempts: int | None = None,
        runner: Callable[..., dict[str, Any] | None] | None = None,
        poll_interval: float = 2.0,
    ):
        from ..config import get_settings

        cfg = get_settings()
        self._db_path = Path(db_path or cfg.scoring_db_path or DEFAULT_DB_PATH).resolve()
        self._max_workers = max_workers or cfg.scoring_workers
        self._max_attempts = max_attempts or cfg.scoring_max_attempts
        # injectable for tests; defaults to the real scorer
        self._runner = runner or self._default_runner
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self._init_db()

    # ── storage ────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # ── public API ─────────────────────────────────────────────────
    def submit(
        self,
        *,
        case_id: str,
        stage: str,
        case_output_dir: str | Path,
        student_id: str = "",
    ) -> str:
        """Enqueue a scoring job; returns the task key. Idempotent."""
        task_key = self._task_key(case_id, stage, student_id)
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM scoring_tasks WHERE task_key = ?", (task_key,)
            ).fetchone()
            if row and row["status"] in ("pending", "running"):
                return task_key  # already queued — no duplicate work
            if row:
                # done/failed → explicit re-score request
                conn.execute(
                    """UPDATE scoring_tasks
                       SET status = 'pending', attempts = 0, last_error = '',
                           case_output_dir = ?, updated_at = ?
                       WHERE task_key = ?""",
                    (str(case_output_dir), now, task_key),
                )
            else:
                conn.execute(
                    """INSERT INTO scoring_tasks
                       (task_key, case_id, stage, student_id, case_output_dir,
                        status, attempts, max_attempts, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
                    (
                        task_key,
                        case_id,
                        stage,
                        student_id,
                        str(case_output_dir),
                        self._max_attempts,
                        now,
                        now,
                    ),
                )
        self._ensure_workers()
        logger.info("[ScoringQueue] submitted %s", task_key)
        return task_key

    def recover(self) -> int:
        """Reset stale `running` rows (crash leftovers) to pending."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE scoring_tasks SET status = 'pending', updated_at = ? "
                "WHERE status = 'running'",
                (_now(),),
            )
        if cur.rowcount:
            logger.warning("[ScoringQueue] recovered %d stale running task(s)", cur.rowcount)
        return cur.rowcount

    def retry_failed(self) -> int:
        """Re-queue all terminal failures (manual ops endpoint)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE scoring_tasks SET status = 'pending', attempts = 0, "
                "last_error = '', updated_at = ? WHERE status = 'failed'",
                (_now(),),
            )
        if cur.rowcount:
            self._ensure_workers()
        return cur.rowcount

    def snapshot(self) -> list[dict[str, Any]]:
        """All tasks newest-first (ops/debug visibility)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scoring_tasks ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]

    def drain(self, timeout: float = 60.0) -> bool:
        """Block until no pending/running jobs remain (tests / shutdown)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM scoring_tasks "
                    "WHERE status IN ('pending', 'running')"
                ).fetchone()
            if row["n"] == 0:
                return True
            time.sleep(0.2)
        return False

    def shutdown(self) -> None:
        self._stop.set()
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=False)
            self._pool = None

    # ── internals ──────────────────────────────────────────────────
    @staticmethod
    def _task_key(case_id: str, stage: str, student_id: str) -> str:
        return f"{case_id}::{stage}::{student_id or '-'}"

    def _default_runner(self, **kwargs: Any) -> dict[str, Any] | None:
        from .scorer import TeachingScorer

        return TeachingScorer().score_stage(
            case_id=kwargs["case_id"],
            stage=kwargs["stage"],
            case_output_dir=Path(kwargs["case_output_dir"]),
            student_id=kwargs.get("student_id", ""),
            run_async=False,
        )

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._pool is None:
                self.recover()
                self._pool = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="scoring-worker",
                )
                for _ in range(self._max_workers):
                    self._pool.submit(self._worker_loop)

    def _claim_next(self) -> sqlite3.Row | None:
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scoring_tasks WHERE status = 'pending' "
                "ORDER BY updated_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cur = conn.execute(
                "UPDATE scoring_tasks SET status = 'running', "
                "attempts = attempts + 1, updated_at = ? WHERE task_key = ? "
                "AND status = 'pending'",
                (now, row["task_key"]),
            )
            if cur.rowcount != 1:  # raced with another worker
                return None
            return conn.execute(
                "SELECT * FROM scoring_tasks WHERE task_key = ?", (row["task_key"],)
            ).fetchone()

    def _worker_loop(self) -> None:
        import time

        while not self._stop.is_set():
            task = self._claim_next()
            if task is None:
                time.sleep(self._poll_interval)
                continue
            self._execute(task)

    def _execute(self, task: sqlite3.Row) -> None:
        task_key = task["task_key"]
        try:
            event = self._runner(
                case_id=task["case_id"],
                stage=task["stage"],
                case_output_dir=task["case_output_dir"],
                student_id=task["student_id"],
            )
            if event is None:
                raise RuntimeError("scorer returned no event (see scorer logs)")
            self._finish(task_key, "done", "")
            logger.info("[ScoringQueue] done %s", task_key)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._handle_failure(task, error)

    def _handle_failure(self, task: sqlite3.Row, error: str) -> None:
        task_key = task["task_key"]
        retriable = task["attempts"] < task["max_attempts"]
        status = "pending" if retriable else "failed"
        with self._connect() as conn:
            conn.execute(
                "UPDATE scoring_tasks SET status = ?, last_error = ?, updated_at = ? "
                "WHERE task_key = ?",
                (status, error[:2000], _now(), task_key),
            )
        level = logging.WARNING if retriable else logging.ERROR
        logger.log(
            level,
            "[ScoringQueue] %s %s (attempt %d/%d): %s",
            "retrying" if retriable else "giving up on",
            task_key,
            task["attempts"],
            task["max_attempts"],
            error,
        )

    def _finish(self, task_key: str, status: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE scoring_tasks SET status = ?, last_error = ?, updated_at = ? "
                "WHERE task_key = ?",
                (status, error, _now(), task_key),
            )


_QUEUE: ScoringTaskQueue | None = None
_QUEUE_LOCK = threading.Lock()


def get_scoring_queue() -> ScoringTaskQueue:
    """Process-wide singleton (env-configurable for tests)."""
    global _QUEUE
    with _QUEUE_LOCK:
        if _QUEUE is None:
            _QUEUE = ScoringTaskQueue()
        return _QUEUE


def submit_scoring(
    *, case_id: str, stage: str, case_output_dir: str | Path, student_id: str = ""
) -> str:
    return get_scoring_queue().submit(
        case_id=case_id,
        stage=stage,
        case_output_dir=case_output_dir,
        student_id=student_id,
    )


__all__ = [
    "ScoringTaskQueue",
    "get_scoring_queue",
    "submit_scoring",
]
