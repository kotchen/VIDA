from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_INITIALIZATION_LOCKS: dict[Path, threading.Lock] = {}
_INITIALIZATION_LOCKS_GUARD = threading.Lock()


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
            factory=_Connection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _initialization_lock(self.path):
            self._initialize_locked()

    def _initialize_locked(self) -> None:
        migration_dir = Path(__file__).parent / "migrations"
        migrations = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            has_history = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            applied = set()
            if has_history:
                applied = {
                    row[0] for row in conn.execute("SELECT version FROM schema_migrations")
                }
            for migration in migrations:
                version = int(migration.stem.split("_", 1)[0])
                if version not in applied:
                    conn.executescript(migration.read_text(encoding="utf-8"))
                    applied.add(version)


def _initialization_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _INITIALIZATION_LOCKS_GUARD:
        return _INITIALIZATION_LOCKS.setdefault(resolved, threading.Lock())
