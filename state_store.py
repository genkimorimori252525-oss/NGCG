"""CCG deterministic system state: SQLite WAL, no model calls, no network.

This is a local single-user store, NOT a security boundary against a local process
running with the same OS permissions. UI and Codex transport are separate.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any, Iterator, Optional


SCHEMA_VERSION = 1

def valid_department(department: str) -> bool:
    """Accept explicit project channel IDs without imposing a team structure."""
    return (isinstance(department, str) and department != "all"
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", department) is not None)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def project_key(root: Path) -> str:
    resolved = str(Path(root).expanduser().resolve())
    if os.name == "nt":
        resolved = resolved.casefold()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:32]


def state_path(root: Path) -> Path:
    override = os.getenv("CCG_STATE_ROOT", "").strip()
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "CCG" / "projects"
    else:
        base = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "ccg" / "projects"
    return base / project_key(root) / "state.sqlite3"


class StateStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.project_id = project_key(self.root)
        self.path = state_path(self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextlib.contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with sqlite3.connect(str(self.path), timeout=20) as db:
            db.execute("PRAGMA busy_timeout=20000")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    project_id TEXT PRIMARY KEY,
                    team_chat_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    git_head TEXT NOT NULL,
                    git_dirty INTEGER NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ck_project_time ON checkpoints(project_id,created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    origin_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id,origin,origin_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    department TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    origin_id TEXT NOT NULL,
                    source_seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id,provenance,origin_id),
                    FOREIGN KEY(source_seq) REFERENCES events(seq)
                );
                CREATE INDEX IF NOT EXISTS msg_department_time ON messages(project_id,department,source_seq);
                CREATE TABLE IF NOT EXISTS deliveries (
                    message_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'PENDING',
                    acknowledged_at TEXT,
                    PRIMARY KEY(message_id,recipient),
                    FOREIGN KEY(message_id) REFERENCES messages(id)
                );
                CREATE TABLE IF NOT EXISTS leases (
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(project_id,task_id)
                );
            """)
            db.execute("INSERT OR IGNORE INTO settings(project_id,team_chat_enabled,updated_at) VALUES (?,0,?)",
                       (self.project_id, utc_now()))
            db.commit()

    def chat_enabled(self) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT team_chat_enabled FROM settings WHERE project_id=?", (self.project_id,)).fetchone()
        return bool(row[0]) if row else False

    def set_chat_enabled(self, enabled: bool) -> None:
        with self.connection(write=True) as db:
            db.execute("UPDATE settings SET team_chat_enabled=?,updated_at=? WHERE project_id=?",
                       (int(enabled), utc_now(), self.project_id))
            self._insert_event(db, "system", "chat-toggle-" + uuid.uuid4().hex, "chat_mode", {"enabled": enabled})

    def departments(self) -> list[str]:
        """List only channels used by this project; general is always available."""
        with self.connection() as db:
            rows = db.execute("SELECT DISTINCT department FROM messages "
                              "WHERE project_id=? AND department != 'general' ORDER BY department",
                              (self.project_id,)).fetchall()
        return [str(row[0]) for row in rows]

    def _insert_event(self, db: sqlite3.Connection, origin: str, origin_id: str, kind: str,
                      payload: dict[str, Any]) -> int:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        db.execute("INSERT OR IGNORE INTO events(project_id,origin,origin_id,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                   (self.project_id, origin, origin_id, kind, data, utc_now()))
        row = db.execute("SELECT seq FROM events WHERE project_id=? AND origin=? AND origin_id=?",
                         (self.project_id, origin, origin_id)).fetchone()
        return int(row[0])

    def append_event(self, origin: str, origin_id: str, kind: str, payload: dict[str, Any]) -> int:
        with self.connection(write=True) as db:
            return self._insert_event(db, origin, origin_id, kind, payload)

    def checkpoint(self, task: str, agent: str, status: str = "IN_PROGRESS", evidence: str = "",
                   head: str = "unknown", dirty: bool = False, notes: str = "") -> str:
        ckid = uuid.uuid4().hex
        with self.connection(write=True) as db:
            db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (ckid, self.project_id, agent, task, status, evidence, head, int(dirty), notes, utc_now()))
            self._insert_event(db, "ccg-checkpoint", ckid, "checkpoint", {
                "checkpoint_id": ckid, "agent": agent, "task": task, "status": status,
                "head": head, "dirty": dirty, "evidence": evidence})
        return ckid

    def checkpoints(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM checkpoints WHERE project_id=? ORDER BY rowid DESC LIMIT ?",
                              (self.project_id, min(max(limit, 1), 1000))).fetchall()
        return [dict(row) for row in rows]

    def post_message(self, department: str, role: str, author_id: str, body: str, *,
                     recipients: tuple[str, ...] = (), provenance: str = "manual",
                     origin_id: Optional[str] = None, thread_id: str = "general") -> str:
        if not valid_department(department):
            raise ValueError("Invalid department")
        if role not in {"operator", "sol", "astra", "system"}:
            raise ValueError("Invalid author role")
        if not isinstance(body, str) or not body.strip() or len(body) > 12000:
            raise ValueError("Message body must contain 1-12000 characters")
        if len(author_id) > 100 or len(thread_id) > 100 or len(provenance) > 100:
            raise ValueError("Message metadata too long")
        origin_id = origin_id or uuid.uuid4().hex
        with self.connection(write=True) as db:
            # Idempotency: the same message origin cannot be inserted twice.
            old = db.execute("SELECT id FROM messages WHERE project_id=? AND provenance=? AND origin_id=?",
                             (self.project_id, provenance, origin_id)).fetchone()
            if old:
                return str(old[0])
            message_id = uuid.uuid4().hex
            seq = self._insert_event(db, provenance, origin_id, "chat_message", {
                "message_id": message_id, "department": department, "role": role,
                "author_id": author_id, "thread_id": thread_id})
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                message_id, self.project_id, department, thread_id, role, author_id,
                body, provenance, origin_id, seq, utc_now()))
            for recipient in set(recipients):
                if not recipient or len(recipient) > 100:
                    raise ValueError("Invalid recipient")
                db.execute("INSERT INTO deliveries(message_id,recipient,state) VALUES(?,?,'PENDING')",
                           (message_id, recipient))
        return message_id

    def messages(self, department: str, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if department != "all" and not valid_department(department):
            raise ValueError("Invalid department")
        sql = "SELECT * FROM messages WHERE project_id=? AND source_seq>?"
        params: list[Any] = [self.project_id, max(after, 0)]
        if department != "all":
            sql += " AND department=?"
            params.append(department)
        sql += " ORDER BY source_seq ASC LIMIT ?"
        params.append(min(max(limit, 1), 200))
        with self.connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def acknowledge(self, message_id: str, recipient: str) -> bool:
        with self.connection(write=True) as db:
            cur = db.execute("UPDATE deliveries SET state='ACK',acknowledged_at=? WHERE message_id=? AND recipient=? AND state='PENDING'",
                             (utc_now(), message_id, recipient))
        return cur.rowcount == 1

    def mailbox(self, recipient: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("""SELECT m.* FROM deliveries d JOIN messages m ON m.id=d.message_id
                    WHERE m.project_id=? AND d.recipient=? AND d.state='PENDING'
                    ORDER BY m.source_seq LIMIT ?""", (self.project_id, recipient, min(max(limit,1), 100))).fetchall()
        return [dict(row) for row in rows]

    def claim(self, task_id: str, owner: str, ttl_seconds: int = 300) -> Optional[int]:
        if not task_id or not owner or not (1 <= ttl_seconds <= 86400):
            raise ValueError("Invalid lease claim")
        now = time.time()
        with self.connection(write=True) as db:
            row = db.execute("SELECT owner,epoch,expires_at FROM leases WHERE project_id=? AND task_id=?",
                             (self.project_id, task_id)).fetchone()
            if row and float(row[2]) > now and row[0] != owner:
                return None
            epoch = (int(row[1]) + 1) if row else 1
            db.execute("INSERT INTO leases(project_id,task_id,owner,epoch,expires_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(project_id,task_id) DO UPDATE SET owner=excluded.owner,epoch=excluded.epoch,expires_at=excluded.expires_at",
                       (self.project_id, task_id, owner, epoch, now + ttl_seconds))
            return epoch

    def has_lease(self, task_id: str, owner: str, epoch: int) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT owner,epoch,expires_at FROM leases WHERE project_id=? AND task_id=?",
                             (self.project_id, task_id)).fetchone()
        return bool(row and row[0] == owner and int(row[1]) == epoch and float(row[2]) > time.time())

    def release(self, task_id: str, owner: str, epoch: int) -> bool:
        with self.connection(write=True) as db:
            # Preserve monotonic fencing epoch even after explicit release.
            cur = db.execute("UPDATE leases SET owner='', expires_at=0 WHERE project_id=? AND task_id=? AND owner=? AND epoch=?",
                             (self.project_id, task_id, owner, epoch))
        return cur.rowcount == 1
