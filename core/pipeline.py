"""Collection pipeline: fetch BYR, persist versions, analyse, notify, back up."""

from __future__ import annotations

import logging

from core.config import data_dir
from core.database import Database
from core.errors import safe_error
from core.locking import single_run
from core.notifications import dispatch, enabled
from core.progress import update as progress

log = logging.getLogger(__name__)


def database_path(cfg):
    return data_dir(cfg) / "insight.sqlite3"


def database(cfg) -> Database:
    return Database(database_path(cfg))


def sync(cfg, names=None, push=False, baseline=False, boards=None, fetchers=None) -> dict:
    from sources import get

    names = names or [name for name in ("byr",) if cfg["sources"][name].get("enabled")]
    report = {"sources": {}, "deliveries": []}
    with single_run(data_dir(cfg) / "run.lock"):
        db = database(cfg)
        try:
            db.recover()
            for name in names:
                progress(cfg, name, "starting")
                run = db.start_run(name)
                counts = {"new": 0, "changed": 0, "unchanged": 0}
                errors = []
                try:
                    fetch = fetchers[name] if fetchers else get(name).fetch
                    fetched, _, errors = fetch(cfg, only=boards if name == "byr" else None, quiet=True)
                    for items in fetched.values():
                        for item in items:
                            change = db.upsert(item, channels=enabled(cfg), baseline=baseline)
                            counts[change] += 1
                    if errors and sum(counts.values()):
                        status = "partial_failed"
                    elif errors:
                        status = "login_expired" if any("login_expired" in str(x) for x in errors) else "failed"
                    elif not counts["new"] and not counts["changed"]:
                        status = "no_new"
                    else:
                        status = "ok"
                except (Exception, SystemExit) as exc:
                    status, message = safe_error(exc)
                    errors.append(message)
                    log.warning("source %s failed: %s", name, status)
                db.finish_run(run, status, counts, errors)
                report["sources"][name] = {"status": status, **counts, "errors": errors}
                progress(cfg, name, "source_finished", status=status, **counts)
            if push and not baseline:
                try:
                    from core.analysis import run_pending

                    report["analysis"] = run_pending(cfg, db)
                except Exception:
                    log.exception("analysis failed")
                    report["analysis"] = {"status": "failed", "message": "分析不可用，继续基础提醒"}
                progress(cfg, "deliveries", "pushing")
                report["deliveries"] = dispatch(cfg, db)
            try:
                from core.backup import automatic

                report["backup"] = automatic(database_path(cfg), cfg.get("backup", {}))
            except Exception:
                log.exception("automatic backup failed")
                report["backup"] = {"status": "failed", "message": "自动备份失败，原库保留；请检查空间和权限"}
            from core.health import inspect

            report["health"] = inspect(cfg, db)
            progress(cfg, "all", "completed")
        finally:
            db.close()
    return report
