from __future__ import annotations

from datetime import datetime, timezone
from sqlite3 import Connection, Row

from ..database import Database
from ..domain import InvalidJobState, JobRecord


class JobRepository:
    def __init__(self, database: Database):
        self._database = database

    def enqueue(self, job: JobRecord) -> JobRecord:
        _validate_fresh_job(job)
        with self._database.transaction(immediate=True) as conn:
            conn.execute(
                f"INSERT INTO jobs ({', '.join(_JOB_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _JOB_COLUMNS)})",
                tuple(getattr(job, column) for column in _JOB_COLUMNS),
            )
            if job.type == "process_episode":
                changed = conn.execute(
                    "UPDATE episodes SET current_job_id=?,status='queued',progress=?,"
                    "message=?,warnings_json='[]',error_code=NULL,error_message=NULL,"
                    "completed_at=NULL,updated_at=? WHERE id=?",
                    (job.id, job.progress, job.message, job.submitted_at, job.episode_id),
                ).rowcount
                if changed != 1:
                    raise KeyError(job.episode_id)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._database.connect() as conn:
            row = _select_job(conn, job_id)
        return None if row is None else _job_from_row(row)

    def get_required(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def claim_next(self, worker_id: str, now: str) -> JobRecord | None:
        with self._database.transaction(immediate=True) as conn:
            row = conn.execute(
                f"SELECT {', '.join(_JOB_COLUMNS)} FROM jobs "
                "WHERE status='queued' ORDER BY submitted_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            _require_episode_ownership(conn, row)
            changed = conn.execute(
                "UPDATE jobs SET status='processing',started_at=?,finished_at=NULL,"
                "cancel_requested_at=NULL,heartbeat_at=?,worker_id=?,progress=1,"
                "message='Preparing',error_code=NULL,error_message=NULL "
                "WHERE id=? AND status='queued'",
                (now, now, worker_id, row["id"]),
            ).rowcount
            if changed != 1:
                return None
            if row["type"] == "process_episode":
                conn.execute(
                    "UPDATE episodes SET status='processing',progress=1,message='Preparing',"
                    "error_code=NULL,error_message=NULL,completed_at=NULL,updated_at=? "
                    "WHERE id=? AND current_job_id=?",
                    (now, row["episode_id"], row["id"]),
                )
            return _job_from_row(_require_job(conn, row["id"]))

    def update_progress(
        self, job_id: str, progress: int, message: str, now: str
    ) -> JobRecord:
        with self._database.transaction(immediate=True) as conn:
            row = _require_job(conn, job_id)
            _require_processing(row)
            _require_episode_ownership(conn, row)
            conn.execute(
                "UPDATE jobs SET progress=?,message=?,heartbeat_at=? "
                "WHERE id=? AND status='processing'",
                (progress, message, now, job_id),
            )
            if row["type"] == "process_episode":
                conn.execute(
                    "UPDATE episodes SET progress=?,message=?,updated_at=? "
                    "WHERE id=? AND current_job_id=?",
                    (progress, message, now, row["episode_id"], job_id),
                )
            return _job_from_row(_require_job(conn, job_id))

    def request_cancel(self, job_id: str, now: str) -> JobRecord:
        with self._database.transaction(immediate=True) as conn:
            row = _require_job(conn, job_id)
            if row["status"] == "canceled":
                return _job_from_row(row)
            _require_episode_ownership(conn, row)
            if row["status"] == "queued":
                conn.execute(
                    "UPDATE jobs SET status='canceled',cancel_requested_at=?,finished_at=?,"
                    "message='Canceled' WHERE id=? AND status='queued'",
                    (now, now, job_id),
                )
                if row["type"] == "process_episode":
                    conn.execute(
                        "UPDATE episodes SET status='canceled',message='Canceled',"
                        "completed_at=?,updated_at=? WHERE id=? AND current_job_id=?",
                        (now, now, row["episode_id"], job_id),
                    )
            elif row["status"] == "processing":
                if row["cancel_requested_at"] is not None:
                    return _job_from_row(row)
                conn.execute(
                    "UPDATE jobs SET cancel_requested_at=?,message='Canceling' "
                    "WHERE id=? AND status='processing'",
                    (now, job_id),
                )
                if row["type"] == "process_episode":
                    conn.execute(
                        "UPDATE episodes SET message='Canceling',updated_at=? "
                        "WHERE id=? AND current_job_id=?",
                        (now, row["episode_id"], job_id),
                    )
            else:
                raise InvalidJobState(job_id, row["status"])
            return _job_from_row(_require_job(conn, job_id))

    def complete(self, job_id: str, now: str) -> JobRecord:
        return self._finish(
            job_id, "completed", now, progress=100, message="Completed"
        )

    def fail(
        self, job_id: str, error_code: str, error_message: str, now: str
    ) -> JobRecord:
        return self._finish(
            job_id,
            "failed",
            now,
            message=error_message,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_canceled(self, job_id: str, now: str) -> JobRecord:
        return self._finish(job_id, "canceled", now, message="Canceled")

    def _finish(
        self,
        job_id: str,
        status: str,
        now: str,
        *,
        progress: int | None = None,
        message: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        with self._database.transaction(immediate=True) as conn:
            row = _require_job(conn, job_id)
            _require_processing(row)
            _require_episode_ownership(conn, row)
            terminal_progress = row["progress"] if progress is None else progress
            changed = conn.execute(
                "UPDATE jobs SET status=?,finished_at=?,heartbeat_at=?,progress=?,message=?,"
                "error_code=?,error_message=? WHERE id=? AND status='processing'",
                (status, now, now, terminal_progress, message, error_code,
                 error_message, job_id),
            ).rowcount
            if changed != 1:
                raise InvalidJobState(job_id, row["status"])
            if row["type"] == "process_episode":
                conn.execute(
                    "UPDATE episodes SET status=?,progress=?,message=?,error_code=?,"
                    "error_message=?,completed_at=?,updated_at=? "
                    "WHERE id=? AND current_job_id=?",
                    (status, terminal_progress, message, error_code, error_message,
                     now, now, row["episode_id"], job_id),
                )
            return _job_from_row(_require_job(conn, job_id))

    def recover_interrupted(self, message: str) -> int:
        now = _utc_now()
        with self._database.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id,episode_id,type,status FROM jobs WHERE status='processing' "
                "ORDER BY submitted_at,id"
            ).fetchall()
            if not rows:
                return 0
            for row in rows:
                _require_episode_ownership(conn, row)
            conn.execute(
                "UPDATE jobs SET status='queued',started_at=NULL,finished_at=NULL,"
                "cancel_requested_at=NULL,heartbeat_at=NULL,worker_id=NULL,progress=0,"
                "message=?,error_code=NULL,error_message=NULL WHERE status='processing'",
                (message,),
            )
            for row in rows:
                if row["type"] == "process_episode":
                    conn.execute(
                        "UPDATE episodes SET status='queued',progress=0,message=?,"
                        "error_code=NULL,error_message=NULL,completed_at=NULL,updated_at=? "
                        "WHERE id=? AND current_job_id=?",
                        (message, now, row["episode_id"], row["id"]),
                    )
            return len(rows)

    def queue_position(self, job_id: str) -> int | None:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT CASE WHEN target.status='queued' THEN ("
                "SELECT COUNT(*) + 1 FROM jobs AS earlier "
                "WHERE earlier.status='queued' AND "
                "(earlier.submitted_at < target.submitted_at OR "
                "(earlier.submitted_at = target.submitted_at AND earlier.id < target.id))"
                ") ELSE NULL END AS position FROM jobs AS target WHERE target.id=?",
                (job_id,),
            ).fetchone()
        return None if row is None else row["position"]


_JOB_COLUMNS = (
    "id", "episode_id", "type", "attempt", "status",
    "provider_profile_revision_id", "submitted_at", "started_at", "finished_at",
    "cancel_requested_at", "heartbeat_at", "worker_id", "progress", "message",
    "error_code", "error_message",
)


def _select_job(conn: Connection, job_id: str) -> Row | None:
    return conn.execute(
        f"SELECT {', '.join(_JOB_COLUMNS)} FROM jobs WHERE id=?", (job_id,)
    ).fetchone()


def _require_job(conn: Connection, job_id: str) -> Row:
    row = _select_job(conn, job_id)
    if row is None:
        raise KeyError(job_id)
    return row


def _require_processing(row: Row) -> None:
    if row["status"] != "processing":
        raise InvalidJobState(row["id"], row["status"])


def _validate_fresh_job(job: JobRecord) -> None:
    if job.status != "queued":
        raise InvalidJobState(job.id, job.status)
    runtime_fields = (
        job.started_at,
        job.finished_at,
        job.cancel_requested_at,
        job.heartbeat_at,
        job.worker_id,
        job.error_code,
        job.error_message,
    )
    if job.progress != 0 or any(value is not None for value in runtime_fields):
        raise ValueError("enqueued job must have fresh queued state")


def _require_episode_ownership(conn: Connection, row: Row) -> None:
    if row["type"] != "process_episode":
        return
    owns_episode = conn.execute(
        "SELECT 1 FROM episodes WHERE id=? AND current_job_id=?",
        (row["episode_id"], row["id"]),
    ).fetchone()
    if owns_episode is None:
        raise InvalidJobState(row["id"], row["status"])


def _job_from_row(row: Row) -> JobRecord:
    return JobRecord(*(row[column] for column in _JOB_COLUMNS))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
