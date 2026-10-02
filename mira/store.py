"""Mira 唯一读写 SQLite 的模块。"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

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


@dataclass
class Memory:
    id: int
    type: str
    content: str
    subject: str | None
    importance: int
    status: str | None
    due_at: date | None
    evidence: list[int]
    source_message_ids: list[int]
    user_locked: bool
    superseded_by: int | None
    created_at: datetime
    updated_at: datetime
    last_recalled_at: datetime | None


@dataclass
class Profile:
    id: int
    content: str
    source: str
    created_at: datetime


@dataclass
class LogEntry:
    id: int
    memory_id: int | None
    actor: str
    op: str
    before: dict | None
    after: dict | None
    created_at: datetime


_UPDATABLE = {"content", "subject", "importance", "status", "due_at", "evidence", "user_locked", "superseded_by"}


def _opt_dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        type=row["type"],
        content=row["content"],
        subject=row["subject"],
        importance=row["importance"],
        status=row["status"],
        due_at=date.fromisoformat(row["due_at"]) if row["due_at"] else None,
        evidence=json.loads(row["evidence_json"]),
        source_message_ids=json.loads(row["source_message_ids_json"]),
        user_locked=bool(row["user_locked"]),
        superseded_by=row["superseded_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_recalled_at=_opt_dt(row["last_recalled_at"]),
    )


def _snapshot(m: Memory) -> dict:
    d = asdict(m)
    for k, v in d.items():
        if isinstance(v, (date, datetime)):
            d[k] = v.isoformat()
    return d


def _validate(type: str, importance: int, status: str | None) -> None:
    if type not in MEMORY_TYPES:
        raise ValueError(f"未知的记忆类型：{type}")
    if not isinstance(importance, int) or not 1 <= importance <= 5:
        raise ValueError(f"importance 必须是 1..5：{importance}")
    if status is not None and status not in COMMITMENT_STATUSES:
        raise ValueError(f"未知的状态：{status}")


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

    # ---------- memories ----------

    def add_memory(
        self,
        type: str,
        content: str,
        *,
        vector: np.ndarray,
        actor: str,
        subject: str | None = None,
        importance: int = 3,
        status: str | None = None,
        due_at: date | None = None,
        evidence: list[int] | None = None,
        source_message_ids: list[int] | None = None,
        user_locked: bool = False,
    ) -> Memory:
        _validate(type, importance, status)
        ts = self._ts()
        with self.transaction():
            cur = self._db.execute(
                "INSERT INTO memories(type, content, subject, importance, status, due_at, evidence_json,"
                " source_message_ids_json, user_locked, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    type,
                    content,
                    subject,
                    importance,
                    status,
                    due_at.isoformat() if due_at else None,
                    json.dumps(evidence or []),
                    json.dumps(source_message_ids or []),
                    int(user_locked),
                    ts,
                    ts,
                ),
            )
            self._put_vector(cur.lastrowid, vector)
            m = self.get_memory(cur.lastrowid)
            self._log(m.id, actor, "add", None, _snapshot(m))
        return m

    def get_memory(self, id: int) -> Memory | None:
        row = self._db.execute("SELECT * FROM memories WHERE id=?", (id,)).fetchone()
        return _memory(row) if row else None

    def update_memory(self, id: int, *, actor: str, vector: np.ndarray | None = None, **fields) -> Memory:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"不能修改的字段：{sorted(unknown)}")
        with self.transaction():
            before = self.get_memory(id)
            if before is None:
                raise KeyError(id)
            _validate(before.type, fields.get("importance", before.importance), fields.get("status", before.status))
            cols = dict(fields)
            if "due_at" in cols:
                cols["due_at"] = cols["due_at"].isoformat() if cols["due_at"] else None
            if "evidence" in cols:
                cols["evidence_json"] = json.dumps(cols.pop("evidence"))
            if "user_locked" in cols:
                cols["user_locked"] = int(cols["user_locked"])
            cols["updated_at"] = self._ts()
            sets = ", ".join(f"{k}=?" for k in cols)
            self._db.execute(f"UPDATE memories SET {sets} WHERE id=?", (*cols.values(), id))
            if vector is not None:
                self._put_vector(id, vector)
            after = self.get_memory(id)
            self._log(id, actor, "update", _snapshot(before), _snapshot(after))
        return after

    def delete_memory(self, id: int, *, actor: str) -> None:
        with self.transaction():
            before = self.get_memory(id)
            if before is None:
                raise KeyError(id)
            self._db.execute("DELETE FROM memories WHERE id=?", (id,))
            self._log(id, actor, "delete", _snapshot(before), None)

    def list_memories(self, type: str | None = None, include_superseded: bool = True) -> list[Memory]:
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        if type is not None:
            sql += " AND type=?"
            args.append(type)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY updated_at DESC, id DESC"
        return [_memory(r) for r in self._db.execute(sql, args)]

    def open_commitments(self) -> list[Memory]:
        rows = self._db.execute(
            "SELECT * FROM memories WHERE type='commitment' AND status IN ('open','overdue')"
            " AND superseded_by IS NULL ORDER BY due_at IS NULL, due_at, id"
        )
        return [_memory(r) for r in rows]

    def load_vectors(self, ids: Iterable[int] | None = None) -> dict[int, np.ndarray]:
        rows = self._db.execute("SELECT memory_id, vector FROM memory_vectors").fetchall()
        wanted = set(ids) if ids is not None else None
        return {
            r["memory_id"]: np.frombuffer(r["vector"], dtype=np.float32)
            for r in rows
            if wanted is None or r["memory_id"] in wanted
        }

    def touch_recalled(self, ids: Iterable[int]) -> None:
        ts = self._ts()
        with self.transaction():
            self._db.executemany("UPDATE memories SET last_recalled_at=? WHERE id=?", [(ts, i) for i in ids])

    def _put_vector(self, memory_id: int, vector: np.ndarray) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO memory_vectors(memory_id, vector) VALUES (?,?)",
            (memory_id, np.asarray(vector, dtype=np.float32).tobytes()),
        )

    def _log(self, memory_id: int | None, actor: str, op: str, before: dict | None, after: dict | None) -> None:
        self._db.execute(
            "INSERT INTO memory_log(memory_id, actor, op, before_json, after_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                memory_id,
                actor,
                op,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
                self._ts(),
            ),
        )

    def recent_log(self, limit: int = 20) -> list[LogEntry]:
        rows = self._db.execute("SELECT * FROM memory_log ORDER BY id DESC LIMIT ?", (limit,))
        return [
            LogEntry(
                id=r["id"],
                memory_id=r["memory_id"],
                actor=r["actor"],
                op=r["op"],
                before=json.loads(r["before_json"]) if r["before_json"] else None,
                after=json.loads(r["after_json"]) if r["after_json"] else None,
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ---------- core profile ----------

    def add_profile(self, content: str, source: str) -> Profile:
        with self.transaction():
            cur = self._db.execute(
                "INSERT INTO core_profile(content, source, created_at) VALUES (?,?,?)", (content, source, self._ts())
            )
        return self.get_profile(cur.lastrowid)

    def get_profile(self, id: int) -> Profile | None:
        row = self._db.execute("SELECT * FROM core_profile WHERE id=?", (id,)).fetchone()
        return Profile(row["id"], row["content"], row["source"], datetime.fromisoformat(row["created_at"])) if row else None

    def current_profile(self) -> Profile | None:
        profiles = self.list_profiles()
        return profiles[0] if profiles else None

    def list_profiles(self) -> list[Profile]:
        rows = self._db.execute("SELECT id FROM core_profile ORDER BY id DESC").fetchall()
        return [self.get_profile(r["id"]) for r in rows]

    # ---------- job state ----------

    def get_job_last_run(self, name: str) -> datetime | None:
        row = self._db.execute("SELECT last_run_at FROM job_state WHERE name=?", (name,)).fetchone()
        return datetime.fromisoformat(row["last_run_at"]) if row else None

    def set_job_last_run(self, name: str, when: datetime) -> None:
        with self.transaction():
            self._db.execute(
                "INSERT INTO job_state(name, last_run_at) VALUES (?,?)"
                " ON CONFLICT(name) DO UPDATE SET last_run_at=excluded.last_run_at",
                (name, when.isoformat()),
            )
