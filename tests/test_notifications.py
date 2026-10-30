import types

from core.database import Database, identity
from core.notifications import Result, dispatch, enabled, message, ntfy, webhook
from core.pipeline import database_path
from core.schema import Item


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_private_message_hides_body():
    title, body = message({"title": "私密标题", "body": "私密正文", "private": True, "channel": "版面", "url": "https://x"})
    assert "私密正文" not in body
    assert title == "私密标题"


def test_public_message_includes_excerpt_and_url():
    _, body = message({"title": "公开", "body": "正文内容", "channel": "版面", "url": "https://bbs.byr.cn/a"})
    assert "正文内容" in body and "https://bbs.byr.cn/a" in body


def test_webhook_requires_a_url():
    assert webhook({}, "t", "b").status == "failed"
    assert webhook({"url": "https://example.test/在这里填"}, "t", "b").status == "failed"


def test_webhook_accepts_2xx():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return FakeResponse(200)

    result = webhook({"url": "https://example.test/hook"}, "标题", "正文", "https://bbs.byr.cn/x", post=post)
    assert result.status == "accepted"
    assert calls[0][1]["title"] == "标题"


def test_webhook_server_error_is_unknown():
    result = webhook({"url": "https://example.test/hook"}, "t", "b", post=lambda *a, **k: FakeResponse(503))
    assert result.status == "unknown"


def test_ntfy_requires_topic():
    assert ntfy({"server": "https://ntfy.sh"}, "t", "b").status == "failed"


def test_ntfy_uses_json_publishing_for_utf8_titles():
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return FakeResponse(200)

    result = ntfy({"server": "https://ntfy.sh", "topic": "topic-1"}, "标题", "正文", "https://x", post=post)
    assert result.status == "accepted"
    assert seen["url"] == "https://ntfy.sh"
    assert seen["json"]["topic"] == "topic-1"
    assert seen["json"]["title"] == "标题"


def test_enabled_lists_only_enabled_channels(cfg):
    cfg["push"]["channels"] = {"webhook": {"enabled": True}, "ntfy": {"enabled": False}}
    assert enabled(cfg) == ["webhook"]


def test_dispatch_marks_accepted(cfg):
    cfg["push"]["channels"] = {"webhook": {"enabled": True, "url": "https://example.test/hook"}}
    db = Database(database_path(cfg))
    item = Item(
        source="byr",
        channel="科研与论文",
        title="虚构帖子",
        url="https://bbs.byr.cn/article/Paper/1",
        body="虚构正文",
        extra={"body_status": "ok"},
    )
    try:
        db.upsert(item, channels=["webhook"])
        results = dispatch(cfg, db, sender=lambda channel, title, body, url: Result("accepted", "ok"))
        assert results[0]["status"] == "accepted"
        assert db.db.execute("SELECT status FROM deliveries").fetchone()[0] == "accepted"
    finally:
        db.close()


def test_dispatch_keeps_unknown_without_retry(cfg):
    cfg["push"]["channels"] = {"webhook": {"enabled": True, "url": "https://example.test/hook"}}
    calls = []

    def sender(channel, title, body, url):
        calls.append(channel)
        return Result("unknown", "不确定")

    db = Database(database_path(cfg))
    item = Item(source="byr", channel="科研与论文", title="虚构", url="https://bbs.byr.cn/article/Paper/2", body="正文", extra={"body_status": "ok"})
    try:
        db.upsert(item, channels=["webhook"])
        results = dispatch(cfg, db, sender=sender)
        assert results[0]["status"] == "unknown"
        assert len(calls) == 1
    finally:
        db.close()
