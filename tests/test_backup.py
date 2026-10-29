import json

import pytest

from core import backup
from core.database import identity
from core.pipeline import database, database_path
from core.schema import Item


def seed(cfg):
    db = database(cfg)
    try:
        item = Item(
            source="byr",
            channel="科研与论文",
            title="虚构帖子",
            url="https://bbs.byr.cn/article/Paper/7",
            body="虚构正文",
            extra={"body_status": "ok"},
        )
        db.upsert(item, channels=["webhook"])
        return identity(item)
    finally:
        db.close()


def test_create_and_verify_snapshot(cfg):
    seed(cfg)
    path = database_path(cfg)
    info = backup.create(path)
    assert info["sha256"]
    verified = backup.verify(path, info["name"])
    assert verified["valid"] is True
    assert backup.listing(path)[0]["name"] == info["name"]


def test_verify_rejects_tampering(cfg):
    seed(cfg)
    path = database_path(cfg)
    info = backup.create(path)
    target = backup.backup_dir(path) / info["name"]
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(Exception):
        backup.verify(path, info["name"])


def test_restore_requires_explicit_fingerprint(cfg):
    seed(cfg)
    path = database_path(cfg)
    info = backup.create(path)
    preview = backup.restore(path, info["name"])
    assert preview["status"] == "confirmation_required"
    assert preview["confirm"]


def test_restore_keeps_delivery_receipts(cfg):
    key = seed(cfg)
    path = database_path(cfg)
    info = backup.create(path)
    db = database(cfg)
    try:
        delivery = db.db.execute("SELECT id FROM deliveries").fetchone()[0]
        db.delivery(delivery, "accepted", "ok")
    finally:
        db.close()
    token = backup.restore(path, info["name"])["confirm"]
    result = backup.restore(path, info["name"], token)
    assert result["status"] == "restored"
    db = database(cfg)
    try:
        assert db.db.execute("SELECT status FROM deliveries").fetchone()[0] == "accepted"
    finally:
        db.close()


def test_recover_copy_does_not_activate(cfg):
    seed(cfg)
    path = database_path(cfg)
    info = backup.create(path)
    result = backup.recover_copy(path, info["name"])
    assert result["status"] == "recovered_copy"
    assert result["active_database_unchanged"] is True
    assert path.exists()


def test_selected_rejects_path_traversal(cfg):
    seed(cfg)
    path = database_path(cfg)
    backup.create(path)
    with pytest.raises(Exception):
        backup.selected(path, "../escape.sqlite3")
