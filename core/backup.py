"""SQLite online snapshots. Caller holds the application writer lock for mutations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.errors import InsightError

REQUIRED_TABLES = {"notices", "deliveries", "sync_runs"}


def digest(path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        yield connection
    finally:
        connection.close()


def check(connection) -> None:
    if [r[0] for r in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise InsightError("backup_invalid", "数据库完整性检查失败，停止操作")
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED_TABLES <= tables:
        raise InsightError("backup_invalid", "不是本项目数据库")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise InsightError("backup_invalid", "数据库引用检查失败")


def snapshot(source, destination, timeout=20) -> None:
    """Never copy a live database file. Failed snapshots never become final backups."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > timeout:
            raise InsightError("backup_timeout", "备份超时，保留现有数据")

    src = dst = None
    try:
        with readonly(source) as src_connection, sqlite3.connect(temporary) as dst_connection:
            src, dst = src_connection, dst_connection
            check(src)
            src.backup(dst, pages=128, progress=progress, sleep=0.05)
            check(dst)
            check(src)
        src.close()
        dst.close()
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        for connection in (src, dst):
            if connection is not None:
                connection.close()
        temporary.unlink(missing_ok=True)


def backup_dir(path):
    return Path(path).parent / "backups"


def create(path, prefix="snapshot"):
    path = Path(path).resolve()
    name = f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.sqlite3"
    target = backup_dir(path) / name
    snapshot(path, target)
    metadata = {"scope": path.parent.name, "sha256": digest(target), "created_at": datetime.now(timezone.utc).isoformat()}
    manifest = target.with_suffix(".json")
    temp = manifest.with_suffix(".json.tmp")
    temp.write_text(json.dumps(metadata), encoding="utf-8")
    os.replace(temp, manifest)
    return {"name": name, **metadata}


def selected(path, name):
    root = backup_dir(path).resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root or candidate.suffix != ".sqlite3" or candidate.is_symlink():
        raise InsightError("backup_invalid", "仅允许当前账号备份目录中的备份文件名")
    return candidate


def verify(path, name):
    candidate = selected(path, name)
    try:
        meta = json.loads(candidate.with_suffix(".json").read_text(encoding="utf-8"))
        if meta["scope"] != Path(path).parent.name or meta["sha256"] != digest(candidate):
            raise ValueError()
        with readonly(candidate) as connection:
            check(connection)
        connection.close()
        return {"name": candidate.name, "valid": True, **meta}
    except (OSError, ValueError, KeyError, sqlite3.Error):
        raise InsightError("backup_invalid", "备份缺失、作用域不符或校验失败") from None


def listing(path):
    result = []
    for candidate in sorted(backup_dir(path).glob("*.sqlite3"), reverse=True):
        try:
            result.append(verify(path, candidate.name))
        except InsightError:
            result.append({"name": candidate.name, "valid": False})
    return result


def automatic(path, config):
    if not config.get("enabled", True):
        return {"status": "disabled"}
    valid = [r for r in listing(path) if r["valid"] and r["name"].startswith("snapshot-")]
    now = datetime.now(timezone.utc)
    interval = max(0.01, float(config.get("interval_hours", 24)))
    if valid and (now - max(datetime.fromisoformat(r["created_at"]) for r in valid)).total_seconds() < interval * 3600:
        return {"status": "not_due"}
    result = create(path)
    # Retention applies ONLY to verified scheduled snapshots in this database directory.
    keep = max(1, int(config.get("keep", 7)))
    valid = sorted(
        [r for r in listing(path) if r["valid"] and r["name"].startswith("snapshot-")],
        key=lambda r: r["created_at"],
        reverse=True,
    )
    for old in valid[keep:]:
        file = selected(path, old["name"])
        file.unlink()
        file.with_suffix(".json").unlink()
    return {"status": "ok", "name": result["name"]}


def restore(path, name, confirmation=None):
    """Explicit fingerprint approval, safety snapshot and receipt merge prevent replay."""
    path = Path(path).resolve()
    verify(path, name)
    token = digest(path) if path.exists() else "new-database"
    if confirmation != token:
        return {
            "status": "confirmation_required",
            "confirm": token,
            "message": "恢复会替换当前数据库；先生成安全快照并合并已投递记录。请核对后带 --confirm 再运行。",
        }
    # This revision refuses in-place replacement of an unreadable database: without
    # its receipts we cannot promise not to replay notifications. Never erase it.
    receipts = []
    if path.exists():
        with readonly(path) as current:
            check(current)
            tables = {r[0] for r in current.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "delivery_receipts" in tables:
                receipts = current.execute("SELECT * FROM delivery_receipts").fetchall()
            old = current.execute(
                "SELECT notice_id,hash,channel,status FROM deliveries WHERE status NOT IN ('pending','failed')"
            ).fetchall()
        current.close()
        safety = create(path, "before-restore")
    else:
        old = []
        safety = None
    staged = path.with_name("restore-" + uuid4().hex + ".sqlite3")
    try:
        snapshot(selected(path, name), staged)
        from core.database import Database

        db = Database(staged)
        try:
            with db.db:
                db.db.executemany("INSERT OR REPLACE INTO delivery_receipts VALUES(?,?,?,?)", receipts + old)
                db.db.execute(
                    "UPDATE deliveries SET status=COALESCE((SELECT r.status FROM delivery_receipts r "
                    "WHERE r.notice_id=deliveries.notice_id AND r.hash=deliveries.hash AND r.channel=deliveries.channel),status)"
                )
                db.db.execute("UPDATE deliveries SET status='unknown' WHERE status='sending'")
                # Historical digest payloads may include already-sent members.
                # Hold the entire old batch rather than risk a partial duplicate.
                db.db.execute(
                    """UPDATE digest_batches SET status='unknown',detail='恢复后需人工核对汇总'
                  WHERE status IN ('pending','failed','sending')"""
                )
                db.db.execute("UPDATE deliveries SET status='unknown' WHERE status='digest_waiting'")
            check(db.db)
        finally:
            db.close()
        os.replace(staged, path)
        return {"status": "restored", "safety_backup": safety["name"] if safety else None}
    finally:
        staged.unlink(missing_ok=True)


def recover_copy(path, name):
    """Restore beside an unreadable current DB without replacing or activating it."""
    verify(path, name)
    target = Path(path).with_name("recovered-" + uuid4().hex + ".sqlite3")
    snapshot(selected(path, name), target)
    from core.database import Database

    db = Database(target)
    try:
        with db.db:
            db.db.execute(
                "UPDATE deliveries SET status='unknown',detail='恢复副本，投递状态需人工核对' "
                "WHERE status IN ('pending','failed','sending','digest_waiting')"
            )
            db.db.execute("UPDATE digest_batches SET status='unknown' WHERE status IN ('pending','failed','sending')")
        check(db.db)
    finally:
        db.close()
    return {
        "status": "recovered_copy",
        "path": str(target),
        "active_database_unchanged": True,
        "message": "副本未激活；损坏库仍保留。请先核对投递和备份之后丢失的记录，再人工安排切换。",
    }
