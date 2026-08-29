"""Tests for the persistent scoring task queue (src.teaching.task_queue)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.teaching.task_queue import ScoringTaskQueue


def _events_spy(tmp_path: Path):
    """Runner factory that records invocations and can be made to fail."""
    calls: list[dict] = []

    def runner(**kwargs) -> dict:
        calls.append(kwargs)
        # emulate TeachingScorer persisting a LearningEvent per job
        out_dir = Path(kwargs["case_output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "teaching").mkdir(exist_ok=True)
        (out_dir / "teaching" / f"{kwargs['stage']}_learning_event.json").write_text(
            json.dumps({"case_id": kwargs["case_id"]}), encoding="utf-8"
        )
        return {"case_id": kwargs["case_id"], "stage": kwargs["stage"]}

    return runner, calls


@pytest.fixture()
def queue(tmp_path: Path) -> ScoringTaskQueue:
    runner, _calls = _events_spy(tmp_path)
    q = ScoringTaskQueue(
        db_path=tmp_path / "tasks.db",
        max_workers=2,
        max_attempts=2,
        runner=runner,
        poll_interval=0.05,
    )
    yield q
    q.shutdown()


class TestSubmitIdempotency:
    def test_duplicate_submit_runs_once(self, queue: ScoringTaskQueue, tmp_path: Path) -> None:
        kwargs = dict(
            case_id="case_1", stage="DS",
            case_output_dir=tmp_path / "out1", student_id="s1",
        )
        queue.submit(**kwargs)
        queue.submit(**kwargs)
        assert queue.drain(timeout=10)
        # wait for pool to settle, then assert single execution
        import time

        time.sleep(0.3)
        with sqlite3.connect(queue._db_path) as conn:  # noqa: SLF001 - test introspection
            rows = conn.execute(
                "SELECT status, attempts FROM scoring_tasks WHERE task_key LIKE 'case_1%'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "done"
        assert rows[0][1] == 1, "duplicate submit must not double-execute"

    def test_done_task_resubmitted_reruns(self, tmp_path: Path) -> None:
        runner, calls = _events_spy(tmp_path)
        queue = ScoringTaskQueue(
            db_path=tmp_path / "tasks_resubmit.db",
            max_workers=1,
            max_attempts=2,
            runner=runner,
            poll_interval=0.05,
        )
        kwargs = dict(
            case_id="case_2", stage="CR",
            case_output_dir=tmp_path / "out2", student_id="s2",
        )
        try:
            queue.submit(**kwargs)
            assert queue.drain(timeout=10)
            assert len(calls) == 1
            queue.submit(**kwargs)  # explicit re-score
            assert queue.drain(timeout=10)
        finally:
            queue.shutdown()
        assert len(calls) == 2, "re-submit after done should execute again"


class TestRetry:
    def test_failure_retries_then_gives_up(self, tmp_path: Path) -> None:
        attempts: list[int] = []

        def flaky_runner(**kwargs) -> dict:
            attempts.append(1)
            raise RuntimeError("LLM exploded")

        q = ScoringTaskQueue(
            db_path=tmp_path / "retry.db",
            max_workers=1,
            max_attempts=2,
            runner=flaky_runner,
            poll_interval=0.05,
        )
        try:
            q.submit(case_id="case_3", stage="PR",
                     case_output_dir=tmp_path / "out3", student_id="s3")
            assert q.drain(timeout=10)
            import time

            time.sleep(0.3)
        finally:
            q.shutdown()
        assert len(attempts) == 2, "exactly max_attempts executions expected"
        tasks = q.snapshot()
        assert tasks[0]["status"] == "failed"
        assert "LLM exploded" in tasks[0]["last_error"]

    def test_retry_failed_requeues(self, queue: ScoringTaskQueue) -> None:
        with queue._connect() as conn:  # noqa: SLF001 - seed a fake failure
            conn.execute(
                "INSERT INTO scoring_tasks (task_key, case_id, stage, student_id,"
                " case_output_dir, status, attempts, max_attempts, last_error,"
                " created_at, updated_at)"
                " VALUES ('c::PR::s', 'c', 'PR', 's', 'x', 'failed', 3, 3, 'boom', '0', '0')"
            )
        assert queue.retry_failed() == 1
        assert queue.drain(timeout=10)


class TestCrashRecovery:
    def test_stale_running_reset_to_pending(self, queue: ScoringTaskQueue) -> None:
        with queue._connect() as conn:  # noqa: SLF001 - simulate crash leftover
            conn.execute(
                "INSERT INTO scoring_tasks (task_key, case_id, stage, student_id,"
                " case_output_dir, status, attempts, max_attempts, created_at, updated_at)"
                " VALUES ('c2::DS::s2', 'c2', 'DS', 's2', 'y', 'running', 1, 3, '0', '0')"
            )
        assert queue.recover() == 1
        statuses = {t["task_key"]: t["status"] for t in queue.snapshot()}
        assert statuses["c2::DS::s2"] == "pending"


class TestSnapshot:
    def test_snapshot_lists_tasks(self, queue: ScoringTaskQueue, tmp_path: Path) -> None:
        queue.submit(case_id="case_s", stage="LC",
                     case_output_dir=tmp_path / "outs", student_id="")
        assert queue.drain(timeout=10)
        snap = queue.snapshot()
        assert snap and snap[0]["case_id"] == "case_s"
        assert snap[0]["status"] == "done"
