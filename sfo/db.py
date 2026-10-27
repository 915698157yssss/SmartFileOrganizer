"""SQLite 持久层（WAL）。

相比 v6 的 4 个 JSON 文件，这里解决的是一致性问题：
- 单文件、事务提交、断电不产生半截数据；
- label 采用 append-only（接受/改判/拒绝都是新证据），可做权重与时间衰减；
- 文件移动 / 撤销有 journal 可追溯。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

from .textutil import now_iso

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS doc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT,
    title       TEXT NOT NULL,
    ext         TEXT DEFAULT '',
    folder      TEXT DEFAULT '',
    size        INTEGER DEFAULT 0,
    mtime       REAL DEFAULT 0,
    sha256      TEXT,
    excerpt     TEXT DEFAULT '',
    text_source TEXT DEFAULT '',
    added_at    TEXT,
    last_seen   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_path ON doc(path);
CREATE INDEX IF NOT EXISTS ix_doc_sha ON doc(sha256);

CREATE TABLE IF NOT EXISTS label (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     INTEGER NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
    target     TEXT NOT NULL,
    source     TEXT NOT NULL,
    weight     REAL DEFAULT 1.0,
    created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_label ON label(doc_id, target, source);
CREATE INDEX IF NOT EXISTS ix_label_target ON label(target);

CREATE TABLE IF NOT EXISTS pending (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER,
    path         TEXT NOT NULL,
    filename     TEXT NOT NULL,
    suggestion   TEXT,
    score        REAL DEFAULT 0,
    alternatives TEXT DEFAULT '',
    reason       TEXT DEFAULT '',
    mode         TEXT DEFAULT 'suggest',
    status       TEXT DEFAULT 'waiting',
    created_at   TEXT,
    resolved_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_pending_status ON pending(status);

CREATE TABLE IF NOT EXISTS task (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     INTEGER REFERENCES doc(id) ON DELETE SET NULL,
    title      TEXT NOT NULL,
    dtype      TEXT,
    due        TEXT,
    priority   INTEGER DEFAULT 1,
    status     TEXT DEFAULT 'candidate',
    action     TEXT,
    confidence REAL DEFAULT 0,
    note       TEXT,
    dedupe     TEXT UNIQUE,
    created_at TEXT,
    done_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_task_status_due ON task(status, due);

CREATE TABLE IF NOT EXISTS journal (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    op         TEXT NOT NULL,
    src        TEXT,
    dst        TEXT,
    sha256     TEXT,
    payload    TEXT DEFAULT '',
    status     TEXT DEFAULT 'done',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_journal_status ON journal(status);

CREATE TABLE IF NOT EXISTS ignore_rule (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT,
    pattern  TEXT,
    enabled  INTEGER DEFAULT 1
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._set_meta("schema_version", str(SCHEMA_VERSION))

    # ------------------------------------------------------------------ 基础
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def counts(self) -> dict:
        with self._lock:
            cur = self._conn
            return {
                "docs": cur.execute("SELECT COUNT(*) FROM doc").fetchone()[0],
                "labels": cur.execute("SELECT COUNT(*) FROM label").fetchone()[0],
                "targets": cur.execute("SELECT COUNT(DISTINCT target) FROM label").fetchone()[0],
                "pending": cur.execute(
                    "SELECT COUNT(*) FROM pending WHERE status IN ('waiting','observed')"
                ).fetchone()[0],
                "tasks_todo": cur.execute(
                    "SELECT COUNT(*) FROM task WHERE status IN ('candidate','todo','doing')"
                ).fetchone()[0],
                "tasks_overdue": cur.execute(
                    "SELECT COUNT(*) FROM task WHERE status IN ('todo','doing') "
                    "AND due IS NOT NULL AND due < date('now','localtime')"
                ).fetchone()[0],
            }

    # ------------------------------------------------------------------ doc
    def upsert_doc(
        self,
        path: str,
        title: str,
        ext: str = "",
        folder: str = "",
        size: int = 0,
        mtime: float = 0,
        sha256: str | None = None,
        excerpt: str = "",
        text_source: str = "",
    ) -> tuple[int, bool]:
        stamp = now_iso()
        with self._lock, self._conn:
            row = self._conn.execute("SELECT id FROM doc WHERE path=?", (path,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE doc SET title=?, ext=?, folder=?, size=?, mtime=?, "
                    "sha256=COALESCE(?, sha256), excerpt=?, text_source=?, last_seen=? "
                    "WHERE id=?",
                    (title, ext, folder, size, mtime, sha256, excerpt, text_source, stamp, row["id"]),
                )
                return int(row["id"]), False
            cur = self._conn.execute(
                "INSERT INTO doc(path,title,ext,folder,size,mtime,sha256,excerpt,"
                "text_source,added_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (path, title, ext, folder, size, mtime, sha256, excerpt, text_source, stamp, stamp),
            )
            return int(cur.lastrowid), True

    def add_virtual_doc(self, title: str, folder: str, excerpt: str = "") -> int:
        """v6 学习数据没有真实文件，用 path=NULL 的虚拟文档承接。"""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM doc WHERE path IS NULL AND title=? AND folder=?",
                (title, folder),
            ).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO doc(path,title,ext,folder,size,mtime,sha256,excerpt,"
                "text_source,added_at,last_seen) VALUES(NULL,?,?,'',0,0,NULL,?,'import',?,?)",
                (title, folder, excerpt, now_iso(), now_iso()),
            )
            return int(cur.lastrowid)

    def get_doc(self, doc_id: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM doc WHERE id=?", (doc_id,)).fetchone()

    def doc_by_path(self, path: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM doc WHERE path=?", (path,)).fetchone()

    def find_doc_by_sha(self, sha: str, exclude_path: str | None = None) -> Optional[sqlite3.Row]:
        with self._lock:
            if exclude_path:
                return self._conn.execute(
                    "SELECT * FROM doc WHERE sha256=? AND (path IS NULL OR path<>?) LIMIT 1",
                    (sha, exclude_path),
                ).fetchone()
            return self._conn.execute(
                "SELECT * FROM doc WHERE sha256=? LIMIT 1", (sha,)
            ).fetchone()

    def set_doc_path(self, doc_id: int, path: str, folder: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE doc SET path=?, folder=?, last_seen=? WHERE id=?",
                (path, folder, now_iso(), doc_id),
            )

    # ---------------------------------------------------------------- label
    def add_label(
        self, doc_id: int, target: str, source: str, weight: float = 1.0
    ) -> Optional[int]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO label(doc_id,target,source,weight,created_at) "
                "VALUES(?,?,?,?,?)",
                (doc_id, target, source, weight, now_iso()),
            )
            if cur.rowcount:
                return int(cur.lastrowid)
            row = self._conn.execute(
                "SELECT id FROM label WHERE doc_id=? AND target=? AND source=?",
                (doc_id, target, source),
            ).fetchone()
            return int(row["id"]) if row else None

    def delete_label_for(self, doc_id: int, target: str, source: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM label WHERE doc_id=? AND target=? AND source=?",
                (doc_id, target, source),
            )

    def training_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.id AS doc_id, d.title, d.ext, d.folder, d.excerpt, "
                "l.id AS label_id, l.target, l.source, l.weight "
                "FROM label l JOIN doc d ON d.id = l.doc_id "
                "ORDER BY l.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- pending
    def pending_add(
        self,
        doc_id: int | None,
        path: str,
        filename: str,
        suggestion: str | None,
        score: float,
        alternatives: Iterable[tuple[str, float]] = (),
        reason: str = "",
        mode: str = "suggest",
        status: str = "waiting",
    ) -> int:
        alts = json.dumps([[t, round(float(s), 4)] for t, s in alternatives], ensure_ascii=False)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM pending WHERE path=? AND status IN ('waiting','observed')",
                (path,),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE pending SET suggestion=?, score=?, alternatives=?, reason=?, "
                    "mode=?, doc_id=? WHERE id=?",
                    (suggestion, score, alts, reason, mode, doc_id, row["id"]),
                )
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO pending(doc_id,path,filename,suggestion,score,alternatives,"
                "reason,mode,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (doc_id, path, filename, suggestion, score, alts, reason, mode, status, now_iso()),
            )
            return int(cur.lastrowid)

    def pending_rows(self, statuses: tuple[str, ...] = ("waiting", "observed")) -> list[dict]:
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM pending WHERE status IN ({marks}) ORDER BY created_at DESC",
                statuses,
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_get(self, pid: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM pending WHERE id=?", (pid,)).fetchone()

    def pending_resolve(self, pid: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE pending SET status=?, resolved_at=? WHERE id=?",
                (status, now_iso(), pid),
            )

    # ----------------------------------------------------------------- task
    def task_add(
        self,
        doc_id: int | None,
        title: str,
        dtype: str,
        due: str | None,
        priority: int,
        action: str,
        confidence: float,
        note: str,
        dedupe: str,
    ) -> Optional[int]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO task(doc_id,title,dtype,due,priority,status,action,"
                "confidence,note,dedupe,created_at) VALUES(?,?,?,?,?,'candidate',?,?,?,?,?)",
                (doc_id, title, dtype, due, priority, action, confidence, note, dedupe, now_iso()),
            )
            if cur.rowcount:
                return int(cur.lastrowid)
            return None

    def task_rows(self, scope: str = "all") -> list[dict]:
        where = "1=1"
        if scope in ("candidate", "todo", "doing", "done", "snoozed", "dismissed"):
            where = f"t.status='{scope}'"
        elif scope == "open":
            where = "t.status IN ('candidate','todo','doing')"
        elif scope == "today":
            where = (
                "t.status IN ('todo','doing') AND t.due IS NOT NULL "
                "AND t.due <= date('now','localtime')"
            )
        elif scope == "week":
            where = (
                "t.status IN ('todo','doing') AND t.due IS NOT NULL "
                "AND t.due <= date('now','localtime','+7 day')"
            )
        elif scope == "overdue":
            where = (
                "t.status IN ('todo','doing') AND t.due IS NOT NULL "
                "AND t.due < date('now','localtime')"
            )
        sql = (
            "SELECT t.*, d.path AS doc_path FROM task t LEFT JOIN doc d ON d.id=t.doc_id "
            f"WHERE {where} ORDER BY (t.due IS NULL), t.due, t.priority DESC, t.id DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def task_get(self, tid: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()

    def task_set_status(self, tid: int, status: str) -> None:
        done = now_iso() if status == "done" else None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE task SET status=?, done_at=? WHERE id=?", (status, done, tid)
            )

    def task_delete(self, tid: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM task WHERE id=?", (tid,))

    def task_update_due(self, tid: int, due: str | None, priority: int | None = None) -> None:
        with self._lock, self._conn:
            if priority is None:
                self._conn.execute("UPDATE task SET due=? WHERE id=?", (due, tid))
            else:
                self._conn.execute(
                    "UPDATE task SET due=?, priority=? WHERE id=?", (due, priority, tid)
                )

    # -------------------------------------------------------------- journal
    def journal_add(
        self,
        op: str,
        src: str,
        dst: str,
        sha256: str,
        payload: dict | None = None,
        status: str = "done",
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO journal(op,src,dst,sha256,payload,status,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (op, src, dst, sha256, json.dumps(payload or {}, ensure_ascii=False), status, now_iso()),
            )
            return int(cur.lastrowid)

    def journal_last_done(self, op: str = "move") -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM journal WHERE status='done' AND op=? ORDER BY id DESC LIMIT 1",
                (op,),
            ).fetchone()

    def journal_rows(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["payload_obj"] = json.loads(item.get("payload") or "{}")
            except ValueError:
                item["payload_obj"] = {}
            out.append(item)
        return out

    def journal_set_status(self, jid: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE journal SET status=? WHERE id=?", (status, jid))

    # ---------------------------------------------------------------- misc
    def backup(self, dest: str) -> None:
        with self._lock:
            target = sqlite3.connect(dest)
            try:
                self._conn.backup(target)
            finally:
                target.close()

    def clear_all(self) -> None:
        with self._lock, self._conn:
            for table in ("label", "pending", "task", "journal", "doc"):
                self._conn.execute(f"DELETE FROM {table}")
