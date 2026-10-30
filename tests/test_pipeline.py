from core.errors import InsightError
from core.pipeline import database, database_path, sync
from core.schema import Item


def fetcher(items, errors=None):
    def fetch(cfg, only=None, quiet=False):
        return {"Paper": items}, {"Paper": {"name": "科研与论文"}}, errors or []

    return fetch


def make_item(title="虚构招募帖", body="虚构正文，报名截止 2026-11-20。"):
    return Item(
        source="byr",
        channel="科研与论文",
        title=title,
        url=f"https://bbs.byr.cn/article/Paper/{abs(hash(title)) % 100000}",
        published="2026-10-20 10:00:00",
        body=body,
        extra={"body_status": "ok"},
    )


def test_sync_persists_new_items(cfg):
    report = sync(cfg, ["byr"], fetchers={"byr": fetcher([make_item()])})
    assert report["sources"]["byr"]["status"] == "ok"
    assert report["sources"]["byr"]["new"] == 1
    db = database(cfg)
    try:
        assert db.db.execute("SELECT count(*) FROM notices").fetchone()[0] == 1
    finally:
        db.close()


def test_second_sync_reports_no_new(cfg):
    sync(cfg, ["byr"], fetchers={"byr": fetcher([make_item()])})
    report = sync(cfg, ["byr"], fetchers={"byr": fetcher([make_item()])})
    assert report["sources"]["byr"]["status"] == "no_new"
    assert report["sources"]["byr"]["unchanged"] == 1


def test_source_failure_is_isolated(cfg):
    def broken(cfg, only=None, quiet=False):
        raise InsightError("network_failed", "虚构网络故障")

    report = sync(cfg, ["byr"], fetchers={"byr": broken})
    assert report["sources"]["byr"]["status"] == "network_failed"
    db = database(cfg)
    try:
        run = db.db.execute("SELECT status,errors FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert run["status"] == "network_failed"
    finally:
        db.close()


def test_login_expired_is_reported_separately(cfg):
    def expired(cfg, only=None, quiet=False):
        return {}, {}, ["login_expired: 会话失效"]

    report = sync(cfg, ["byr"], fetchers={"byr": expired})
    assert report["sources"]["byr"]["status"] == "login_expired"


def test_baseline_creates_no_pending_deliveries(cfg):
    cfg["push"]["channels"] = {"webhook": {"enabled": True, "url": "https://example.test/hook"}}
    sync(cfg, ["byr"], baseline=True, fetchers={"byr": fetcher([make_item()])})
    db = database(cfg)
    try:
        assert db.db.execute("SELECT count(*) FROM deliveries WHERE status='pending'").fetchone()[0] == 0
        assert db.db.execute("SELECT count(*) FROM deliveries WHERE status='baseline'").fetchone()[0] == 1
    finally:
        db.close()


def test_database_path_is_configurable(cfg, tmp_path):
    assert database_path(cfg) == tmp_path / "data" / "insight.sqlite3"
