"""Local reader state is independent of the collection DB and its long-running lock."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

from core.database import now
from core.errors import InsightError

log = logging.getLogger(__name__)


@contextmanager
def source(path):
    if not Path(path).exists():
        raise InsightError("no_data", "尚无采集记录，请先运行 run.py scan")
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        # WAL readers may need the -shm file; the service user owns the directory.
        yield con
    finally:
        con.close()


class Reader:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise InsightError("reader_corrupt", "阅读记录完整性异常，原文件已保留")
            db.executescript(
                """
              CREATE TABLE IF NOT EXISTS states (
                notice_id TEXT PRIMARY KEY, read_hash TEXT, favorite INTEGER NOT NULL DEFAULT 0,
                hidden_hash TEXT, last_opened TEXT);
              CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, notice_id TEXT NOT NULL, hash TEXT NOT NULL,
                profile_hash TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL,
                synced INTEGER NOT NULL DEFAULT 0);
            """
            )
        self.backup()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=3000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def states(self):
        with self.connect() as db:
            return {r["notice_id"]: dict(r) for r in db.execute("SELECT * FROM states")}

    def events(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY created_at,id").fetchall()
            return {(r["notice_id"], r["hash"], r["profile_hash"]): dict(r) for r in rows}

    def mutate(self, row, action, value, profile):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO states(notice_id) VALUES(?)", (row["id"],))
            if action == "read":
                db.execute(
                    "UPDATE states SET read_hash=?,last_opened=? WHERE notice_id=?",
                    (row["hash"] if value else None, now(), row["id"]),
                )
            elif action == "favorite":
                db.execute("UPDATE states SET favorite=? WHERE notice_id=?", (int(value), row["id"]))
            elif action == "hidden":
                db.execute("UPDATE states SET hidden_hash=? WHERE notice_id=?", (row["hash"] if value else None, row["id"]))
                self._event(db, row, profile, "irrelevant" if value else "unsure")
            elif action == "feedback":
                self._event(db, row, profile, value)
            else:
                raise InsightError("invalid_action", "不支持的阅读操作")

    def _event(self, db, row, profile, label):
        if label not in ("important", "relevant", "irrelevant", "unsure"):
            raise InsightError("invalid_action", "反馈标签无效")
        old = db.execute(
            "SELECT label FROM events WHERE notice_id=? AND hash=? AND profile_hash=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["id"], row["hash"], profile.fingerprint()),
        ).fetchone()
        if old and old["label"] == label:
            return
        # Microsecond ordering handles fast undo/reapply without random UUID order.
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,0)",
            (uuid4().hex, row["id"], row["hash"], profile.fingerprint(), label, stamp),
        )

    def flush(self, cfg, main_path):
        from core.config import data_dir
        from core.locking import single_run

        try:
            with self.connect() as reader:
                events = [
                    dict(r)
                    for r in reader.execute("SELECT * FROM events WHERE synced=0 ORDER BY created_at,id LIMIT 50")
                ]
            if not events:
                return 0
            with single_run(data_dir(cfg) / "run.lock"):
                # Close the read connection and take the writer lock before using
                # the main DB, so UI writes never wait for a network scan.
                from core.database import Database

                main = Database(main_path)
                try:
                    for event in events:
                        marker = "reader-event:" + event["id"]
                        with main.db:
                            if not main.db.execute("SELECT 1 FROM feedback WHERE note=?", (marker,)).fetchone():
                                main.db.execute(
                                    """INSERT INTO feedback(notice_id,content_hash,profile_hash,label,note,created_at)
                                  VALUES(?,?,?,?,?,?)""",
                                    (
                                        event["notice_id"],
                                        event["hash"],
                                        event["profile_hash"],
                                        event["label"],
                                        marker,
                                        event["created_at"],
                                    ),
                                )
                        with self.connect() as reader:
                            reader.execute("UPDATE events SET synced=1 WHERE id=?", (event["id"],))
                finally:
                    main.close()
            return len(events)
        except (InsightError, sqlite3.Error, OSError) as exc:
            # The durable sidecar queue is retried after the next collection.
            log.warning("feedback sync deferred: %s", type(exc).__name__)
            return 0

    def backup(self):
        directory = self.path.parent / "reader-backups"
        directory.mkdir(exist_ok=True)
        target = directory / (datetime.now(timezone.utc).strftime("%Y%m%d") + ".sqlite3")
        if target.exists():
            return
        tmp = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            with self.connect() as src:
                dst = sqlite3.connect(tmp)
                try:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise InsightError("reader_corrupt", "阅读记录备份校验失败")
                finally:
                    dst.close()
            os.replace(tmp, target)
            for old in sorted(directory.glob("*.sqlite3"))[:-7]:
                old.unlink()
        finally:
            tmp.unlink(missing_ok=True)


def get_rows(path):
    with source(path) as db:
        # Release the read transaction BEFORE doing text analysis or rendering.
        return [
            dict(r)
            for r in db.execute(
                """SELECT n.*,
          COALESCE((SELECT MIN(created_at) FROM deliveries d WHERE d.notice_id=n.id AND d.revision=n.revision),
                   n.updated_at) AS content_seen
          FROM notices n WHERE source='byr'"""
            )
        ]


def feedbacks(path, profile_hash):
    with source(path) as db:
        return {
            (r["notice_id"], r["content_hash"], r["profile_hash"]): {**dict(r), "synced": 1}
            for r in db.execute("SELECT * FROM feedback WHERE profile_hash=? ORDER BY id", (profile_hash,))
        }


def run_status(cfg, path):
    result = {}
    with source(path) as db:
        name = "byr"
        latest = db.execute(
            "SELECT status,started_at,finished_at,total FROM sync_runs WHERE source=? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()
        good = db.execute(
            "SELECT finished_at,total FROM sync_runs WHERE source=? AND status IN ('ok','no_new') ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        state = latest["status"] if latest else "never"
        if state == "running" and datetime.now(timezone.utc) - datetime.fromisoformat(latest["started_at"]) > timedelta(hours=1):
            state = "stuck"
        if good and datetime.now(timezone.utc) - datetime.fromisoformat(good["finished_at"]) > timedelta(hours=24) and state in ("ok", "no_new"):
            state = "stale"
        result[name] = {"status": state, "last_success": good["finished_at"] if good else None, "total": latest["total"] if latest else 0}
    return result
