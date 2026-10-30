"""SQLite notice versions and a durable, per-channel outbox.

Single writer (the scan process) is guaranteed by the OS lock in
``core.locking``; the web process only reads. WAL keeps readers from blocking
on the writer, which matters when a long scan commits while the page polls.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.errors import InsightError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(item) -> str:
    # Whitespace/reply-count changes are not substantive original-post updates.
    fields = [item.title, item.channel, item.author, item.published, re.sub(r"\s+", " ", item.body).strip()]
    if item.extra.get("attachments"):
        fields.append(sorted((a["name"], a["url"]) for a in item.extra["attachments"]))
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def identity(item) -> str:
    match = re.search(r"/article/([^/]+)/(\d+)", item.url)
    if item.source == "byr" and match:
        return f"byr:{match[1]}:{match[2]}"
    return f"{item.source}:{item.key()}"


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=20)
        try:
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("integrity check failed")
            # One writer, one host, local disk: WAL is the right trade-off and
            # lets the reader keep serving while a scan commits.
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
        except sqlite3.DatabaseError:
            self.db.close()
            raise InsightError("storage_corrupt", "本地数据库完整性检查未通过；已停止写入，请保留原文件并恢复备份") from None
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS notices (
          id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
          published TEXT, origin TEXT, body TEXT, private INTEGER NOT NULL,
          hash TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
          first_seen TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries (
          id INTEGER PRIMARY KEY, notice_id TEXT NOT NULL REFERENCES notices(id),
          revision INTEGER NOT NULL, hash TEXT NOT NULL, channel TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
          receipt TEXT, detail TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(notice_id,revision,channel));
        CREATE TABLE IF NOT EXISTS sync_runs (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT, status TEXT NOT NULL, total INTEGER DEFAULT 0,
          new_count INTEGER DEFAULT 0, changed_count INTEGER DEFAULT 0,
          unchanged_count INTEGER DEFAULT 0, errors TEXT);
        CREATE TABLE IF NOT EXISTS delivery_receipts (
          notice_id TEXT NOT NULL, hash TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL,
          PRIMARY KEY(notice_id,hash,channel));
        CREATE TABLE IF NOT EXISTS analysis_cache (
          cache_key TEXT PRIMARY KEY, notice_id TEXT NOT NULL, revision INTEGER NOT NULL,
          content_hash TEXT NOT NULL, profile_hash TEXT NOT NULL, strategy TEXT NOT NULL,
          engine TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL,
          retry_after TEXT, UNIQUE(notice_id,revision,cache_key));
        CREATE TABLE IF NOT EXISTS digest_batches (
          id INTEGER PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
          created_at TEXT NOT NULL, detail TEXT, channel TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS digest_members (
          delivery_id INTEGER PRIMARY KEY REFERENCES deliveries(id),
          batch_id INTEGER NOT NULL REFERENCES digest_batches(id));
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY, notice_id TEXT NOT NULL, content_hash TEXT NOT NULL,
          profile_hash TEXT NOT NULL, label TEXT NOT NULL, note TEXT NOT NULL,
          explanation_quality INTEGER, created_at TEXT NOT NULL);
        """
        )
        self._migrate()
        self.db.execute(
            """INSERT OR IGNORE INTO delivery_receipts
          SELECT notice_id,hash,channel,status FROM deliveries WHERE status NOT IN ('pending','failed')"""
        )
        self.db.commit()

    def _migrate(self) -> None:
        """Additive migrations only; never rewrite or drop user data."""
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(digest_batches)")}
        if "channel" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE digest_batches ADD COLUMN channel TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self.db.close()

    def start_run(self, source: str) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO sync_runs(source,started_at,status) VALUES(?,?,?)", (source, now(), "running")
            ).lastrowid

    def finish_run(self, run, status, counts=None, errors=None) -> None:
        counts = counts or {}
        with self.db:
            self.db.execute(
                """UPDATE sync_runs SET finished_at=?,status=?,total=?,new_count=?,
              changed_count=?,unchanged_count=?,errors=? WHERE id=?""",
                (
                    now(),
                    status,
                    sum(counts.values()),
                    counts.get("new", 0),
                    counts.get("changed", 0),
                    counts.get("unchanged", 0),
                    json.dumps(errors or [], ensure_ascii=False),
                    run,
                ),
            )

    def upsert(self, item, channels=(), baseline=False) -> str:
        key, stamp = identity(item), now()
        old = self.db.execute("SELECT * FROM notices WHERE id=?", (key,)).fetchone()
        content_ready = item.extra.get("body_status", "ok") in ("ok", "attachment_only", "image_only")
        if old and not content_ready:
            # A transient body failure must not erase a previously captured notice.
            return "unchanged"
        digest = content_hash(item)
        change = "new" if old is None else ("unchanged" if old["hash"] == digest else "changed")
        revision = 1 if old is None else old["revision"] + (change == "changed")
        payload = json.dumps(item.to_dict(), ensure_ascii=False)
        with self.db:
            self.db.execute(
                """INSERT INTO notices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,published=excluded.published,
              origin=excluded.origin,body=excluded.body,private=excluded.private,hash=excluded.hash,
              revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at""",
                (
                    key,
                    item.source,
                    item.title,
                    item.url,
                    item.published,
                    item.channel,
                    item.body,
                    item.private,
                    digest,
                    revision,
                    payload,
                    old["first_seen"] if old else stamp,
                    old["updated_at"] if old and change == "unchanged" else stamp,
                ),
            )
            # Queue even unchanged items for newly enabled channels; UNIQUE prevents duplicates.
            if not baseline and content_ready:
                for channel in channels:
                    previous = self.db.execute(
                        "SELECT status FROM delivery_receipts WHERE notice_id=? AND hash=? AND channel=?",
                        (key, digest, channel),
                    ).fetchone()
                    self.db.execute(
                        """INSERT OR IGNORE INTO deliveries
                      (notice_id,revision,hash,channel,payload,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?)""",
                        (key, revision, digest, channel, payload, previous[0] if previous else "pending", stamp, stamp),
                    )
            elif baseline:
                for channel in channels:
                    self.db.execute(
                        """INSERT OR IGNORE INTO deliveries
                      (notice_id,revision,hash,channel,payload,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,'baseline',?,?)""",
                        (key, revision, digest, channel, payload, stamp, stamp),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO delivery_receipts VALUES(?,?,?,?)", (key, digest, channel, "baseline")
                    )
        return change

    def recover(self) -> None:
        with self.db:
            self.db.execute(
                "UPDATE digest_batches SET status='unknown',detail='发送中退出，需人工核对' WHERE status='sending'"
            )
            self.db.execute(
                "UPDATE deliveries SET status='unknown',detail='发送过程中退出，需人工核对' WHERE status='sending'"
            )
            self.db.execute(
                "UPDATE sync_runs SET status='interrupted',finished_at=? WHERE status='running'", (now(),)
            )

    def pending(self, channels):
        if not channels:
            return []
        marks = ",".join("?" for _ in channels)
        return self.db.execute(
            f"SELECT * FROM deliveries WHERE status IN ('pending','failed') AND channel IN ({marks}) ORDER BY id",
            tuple(channels),
        ).fetchall()

    def delivery(self, delivery_id, status, detail="", receipt=None, attempt=False) -> None:
        with self.db:
            self.db.execute(
                """UPDATE deliveries SET status=?,detail=?,receipt=COALESCE(?,receipt),
              attempts=attempts+?,updated_at=? WHERE id=?""",
                (status, detail, receipt, int(attempt), now(), delivery_id),
            )
            if status not in ("pending", "failed"):
                self.db.execute(
                    """INSERT OR REPLACE INTO delivery_receipts
                  SELECT notice_id,hash,channel,status FROM deliveries WHERE id=?""",
                    (delivery_id,),
                )

    def status(self) -> dict:
        return {
            "notices": self.db.execute("SELECT count(*) FROM notices").fetchone()[0],
            "runs": [dict(x) for x in self.db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 10")],
            "deliveries": [
                dict(x) for x in self.db.execute("SELECT status,count(*) AS count FROM deliveries GROUP BY status")
            ],
        }

    def history(self, limit=20):
        return [
            dict(x)
            for x in self.db.execute(
                "SELECT id,title,source,published,origin,url,revision FROM notices ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        ]
