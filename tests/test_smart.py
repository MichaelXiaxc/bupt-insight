import types

from core.database import Database, identity
from core.notifications import Result
from core.pipeline import database_path
from core.profile import Profile
from core.schema import Item
from core.smart import dispatch_digests, feedback_route, queue_digest, route


def result(**overrides):
    value = dict(confidence=0.9, deadline=types.SimpleNamespace(value=None), importance="normal", relevance="medium")
    value.update(overrides)
    return types.SimpleNamespace(**value)


def test_mandatory_item_is_always_immediate():
    assert route({"title": "期末考试安排", "body": "务必参加"}, result(), "ok", Profile()) == "immediate"


def test_low_confidence_falls_back_to_immediate():
    assert route({"title": "普通帖子", "body": "正文"}, result(confidence=0.4), "ok", Profile()) == "immediate"


def test_failed_analysis_falls_back_to_immediate():
    assert route({"title": "普通帖子", "body": "正文"}, None, "fallback", Profile()) == "immediate"


def test_ordinary_medium_relevance_is_digested():
    assert route({"title": "普通帖子", "body": "正文"}, result(), "ok", Profile()) == "digest"


def test_high_relevance_is_immediate_by_default():
    assert route({"title": "普通帖子", "body": "正文"}, result(relevance="high"), "ok", Profile()) == "immediate"


def test_imminent_deadline_is_immediate():
    import datetime

    soon = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert route({"title": "活动", "body": "报名"}, result(deadline=types.SimpleNamespace(value=soon)), "ok", Profile()) == "immediate"


def test_explicit_irrelevant_feedback_archives_but_protects_important():
    profile = Profile()
    assert feedback_route({"title": "普通", "body": ""}, result(relevance="low"), "ok", profile, {"label": "irrelevant"}) == "archive"
    assert feedback_route({"title": "普通", "body": ""}, result(importance="high"), "ok", profile, {"label": "irrelevant"}) == "immediate"
    assert feedback_route({"title": "普通", "body": ""}, result(), "ok", profile, {"label": "important"}) == "immediate"


def seed_deliveries(cfg, count):
    db = Database(database_path(cfg))
    rows = []
    for index in range(count):
        item = Item(
            source="byr",
            channel="科研与论文",
            title=f"虚构帖子 {index}",
            url=f"https://bbs.byr.cn/article/Paper/{1000 + index}",
            body="虚构正文",
            extra={"body_status": "ok"},
        )
        db.upsert(item, channels=["webhook"])
        row = db.db.execute("SELECT * FROM deliveries WHERE notice_id=?", (identity(item),)).fetchone()
        rows.append((row, item.to_dict(), None))
    return db, rows


def test_digest_batches_group_five_members(cfg):
    db, rows = seed_deliveries(cfg, 7)
    try:
        queue_digest(db, rows, "webhook")
        batches = db.db.execute("SELECT * FROM digest_batches ORDER BY id").fetchall()
        assert [batch["channel"] for batch in batches] == ["webhook", "webhook"]
        assert len(__import__("json").loads(batches[0]["payload"])) == 5
        assert db.db.execute("SELECT count(*) FROM deliveries WHERE status='digest_waiting'").fetchone()[0] == 7
    finally:
        db.close()


def test_dispatch_digest_uses_batch_channel(cfg):
    db, rows = seed_deliveries(cfg, 2)
    sent = []

    def sender(channel, title, body, url):
        sent.append((channel, title))
        return Result("accepted", "ok")

    try:
        queue_digest(db, rows, "webhook")
        results = dispatch_digests(cfg, db, sender)
        assert results[0]["channel"] == "webhook"
        assert sent and sent[0][0] == "webhook"
        assert db.db.execute("SELECT status FROM deliveries LIMIT 1").fetchone()[0] == "accepted"
    finally:
        db.close()
