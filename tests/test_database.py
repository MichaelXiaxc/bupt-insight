import json

import pytest

from core.database import Database, content_hash, identity


def open_db(cfg):
    from core.pipeline import database_path

    return Database(database_path(cfg))


def test_identity_uses_board_and_article_id(cfg, notice):
    assert identity(notice) == "byr:Paper:12345"


def test_new_unchanged_and_changed_versions(cfg, notice):
    db = open_db(cfg)
    try:
        assert db.upsert(notice) == "new"
        first = db.db.execute("SELECT * FROM notices WHERE id=?", (identity(notice),)).fetchone()
        assert first["revision"] == 1

        assert db.upsert(notice) == "unchanged"
        same = db.db.execute("SELECT * FROM notices WHERE id=?", (identity(notice),)).fetchone()
        assert same["updated_at"] == first["updated_at"]
        assert same["revision"] == 1

        notice.body += "\n补充说明。"
        assert db.upsert(notice) == "changed"
        changed = db.db.execute("SELECT * FROM notices WHERE id=?", (identity(notice),)).fetchone()
        assert changed["revision"] == 2
        assert changed["updated_at"] >= first["updated_at"]
    finally:
        db.close()


def test_transient_body_failure_does_not_erase_content(cfg, notice):
    import dataclasses

    db = open_db(cfg)
    try:
        db.upsert(notice)
        broken = dataclasses.replace(notice, body="", extra={"body_status": "network_failed"})
        assert db.upsert(broken) == "unchanged"
        row = db.db.execute("SELECT body FROM notices WHERE id=?", (identity(notice),)).fetchone()
        assert row["body"] == notice.body
    finally:
        db.close()


def test_outbox_is_per_channel_and_deduplicated(cfg, notice):
    db = open_db(cfg)
    try:
        db.upsert(notice, channels=["webhook", "ntfy"])
        rows = db.db.execute("SELECT channel,status FROM deliveries ORDER BY channel").fetchall()
        assert [(r["channel"], r["status"]) for r in rows] == [("ntfy", "pending"), ("webhook", "pending")]

        db.upsert(notice, channels=["webhook", "ntfy"])
        assert db.db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 2
    finally:
        db.close()


def test_baseline_queues_without_notifying(cfg, notice):
    db = open_db(cfg)
    try:
        db.upsert(notice, channels=["webhook"], baseline=True)
        row = db.db.execute("SELECT status FROM deliveries").fetchone()
        assert row["status"] == "baseline"
        assert db.pending(["webhook"]) == []
    finally:
        db.close()


def test_receipts_block_replay_for_known_content(cfg, notice):
    db = open_db(cfg)
    try:
        db.upsert(notice, channels=["webhook"])
        delivery = db.db.execute("SELECT id FROM deliveries").fetchone()[0]
        db.delivery(delivery, "accepted", "ok")
        # A later scan of the same version must not create a second pending item.
        db.upsert(notice, channels=["webhook"])
        assert db.db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1
        assert db.pending(["webhook"]) == []
    finally:
        db.close()


def test_database_uses_wal_and_foreign_keys(cfg):
    db = open_db(cfg)
    try:
        assert db.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        db.close()


def test_recover_marks_inflight_rows_unknown(cfg, notice):
    db = open_db(cfg)
    try:
        db.upsert(notice, channels=["webhook"])
        delivery = db.db.execute("SELECT id FROM deliveries").fetchone()[0]
        db.delivery(delivery, "sending", "in flight")
        db.recover()
        assert db.db.execute("SELECT status FROM deliveries WHERE id=?", (delivery,)).fetchone()[0] == "unknown"
    finally:
        db.close()


def test_content_hash_ignores_whitespace_noise(cfg, notice):
    before = content_hash(notice)
    notice.body = notice.body.replace("\n", "\n\n")
    assert content_hash(notice) == before
