from datetime import datetime, timedelta, timezone

from core.health import inspect
from core.pipeline import database


def test_healthy_after_successful_run(cfg):
    cfg["backup"]["enabled"] = False
    db = database(cfg)
    try:
        run = db.start_run("byr")
        db.finish_run(run, "ok", {"new": 1})
        value = inspect(cfg, db)
        assert value["sources"]["byr"]["status"] == "healthy"
        assert value["status"] == "healthy"
    finally:
        db.close()


def test_stale_when_no_success_for_a_day(cfg):
    cfg["backup"]["enabled"] = False
    db = database(cfg)
    try:
        run = db.start_run("byr")
        db.finish_run(run, "ok", {"new": 1})
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
        with db.db:
            db.db.execute("UPDATE sync_runs SET finished_at=? WHERE id=?", (old, run))
        value = inspect(cfg, db)
        assert value["sources"]["byr"]["status"] == "stale"
        assert value["status"] == "attention"
    finally:
        db.close()


def test_latest_failure_is_reported(cfg):
    cfg["backup"]["enabled"] = False
    db = database(cfg)
    try:
        run = db.start_run("byr")
        db.finish_run(run, "login_expired", {}, ["login_expired: 会话失效"])
        value = inspect(cfg, db)
        assert value["sources"]["byr"]["status"] == "latest_failed"
    finally:
        db.close()


def test_no_runs_is_never_succeeded(cfg):
    cfg["backup"]["enabled"] = False
    db = database(cfg)
    try:
        value = inspect(cfg, db)
        assert value["sources"]["byr"]["status"] == "never_succeeded"
    finally:
        db.close()
