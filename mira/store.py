"""Mira 唯一读写 SQLite 的模块。"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mira import clock
from mira.textutil import estimate_tokens

MEMORY_TYPES = ("fact", "person", "commitment", "pattern", "episode")
COMMITMENT_STATUSES = ("open", "done", "dropped", "overdue")
APPROACHES = ("comfort", "normal", "raise_issue", "crisis")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL, batch_id TEXT, created_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}', processed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, type TEXT NOT NULL, content TEXT NOT NULL, subject TEXT, importance INTEGER NOT NULL DEFAULT 3, status TEXT, due_at TEXT, evidence_json TEXT NOT NULL DEFAULT '[]', source_message_ids_json TEXT NOT NULL DEFAULT '[]', user_locked INTEGER NOT NULL DEFAULT 0, superseded_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_recalled_at TEXT);
CREATE TABLE IF NOT EXISTS memory_vectors(memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE, vector BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS core_profile(id INTEGER PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_log(id INTEGER PRIMARY KEY, memory_id INTEGER, actor TEXT NOT NULL, op TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_state(name TEXT PRIMARY KEY, last_run_at TEXT NOT NULL);
"""


@dataclass
class Message:
    id: int
    role: str
    content: str
    batch_id: str | None
    created_at: datetime
    meta: dict
    processed: bool


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        batch_id=row["batch_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        meta=json.loads(row["meta_json"]),
        processed=bool(row["processed"]),
    )


class Store:
    def __init__(self, path: str | Path, now: Callable[[], datetime] = clock.now):
        self._now = now
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """可嵌套的事务：只有最外层负责 BEGIN/COMMIT/ROLLBACK。"""
        outer = self._depth == 0
        if outer:
            self._db.execute("BEGIN")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if outer:
                self._db.execute("ROLLBACK")
            raise
        self._depth -= 1
        if outer:
            self._db.execute("COMMIT")

    def _ts(self) -> str:
        return self._now().isoformat()

    # ---------- messages ----------

    def add_message(
        self, role: str, content: str, batch_id: str | None = None, meta: dict | None = None
    ) -> Message:
        with self.transaction():
            cur = self._db.execute(
                "INSERT INTO messages(role, content, batch_id, created_at, meta_json) VALUES (?,?,?,?,?)",
                (role, content, batch_id, self._ts(), json.dumps(meta or {}, ensure_ascii=False)),
            )
        return self._get_message(cur.lastrowid)

    def _get_message(self, id: int) -> Message:
        return _message(self._db.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone())

    def list_messages(self, before_id: int | None = None, limit: int = 50) -> list[Message]:
        rows = self._db.execute(
            "SELECT * FROM messages WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**62, limit),
        ).fetchall()
        return [_message(r) for r in reversed(rows)]

    def recent_messages(self, max_tokens: int, exclude_batch: str | None = None) -> list[Message]:
        out: list[Message] = []
        used = 0
        for row in self._db.execute("SELECT * FROM messages ORDER BY id DESC"):
            if exclude_batch is not None and row["batch_id"] == exclude_batch:
                continue
            cost = estimate_tokens(row["content"])
            if used + cost > max_tokens:
                break
            used += cost
            out.append(_message(row))
        return list(reversed(out))

    def messages_in_batch(self, batch_id: str) -> list[Message]:
        rows = self._db.execute("SELECT * FROM messages WHERE batch_id=? ORDER BY id", (batch_id,))
        return [_message(r) for r in rows]

    def unprocessed_messages(self) -> list[Message]:
        rows = self._db.execute("SELECT * FROM messages WHERE processed=0 ORDER BY id")
        return [_message(r) for r in rows]

    def mark_processed(self, ids: Iterable[int]) -> None:
        with self.transaction():
            self._db.executemany("UPDATE messages SET processed=1 WHERE id=?", [(i,) for i in ids])

    def last_unanswered_batch(self) -> str | None:
        row = self._db.execute("SELECT role, batch_id FROM messages ORDER BY id DESC LIMIT 1").fetchone()
        if row is None or row["role"] != "user":
            return None
        return row["batch_id"]
