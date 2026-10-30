"""Read-only collection evidence; a scheduler exit code is not enough."""

from __future__ import annotations

from datetime import datetime, timezone


def inspect(cfg, db, at=None) -> dict:
    at = at or datetime.now(timezone.utc)
    threshold = max(1, float(cfg.get("health", {}).get("stale_hours", 24)))
    sources = {}
    for name in ("byr",):
        if not cfg.get("sources", {}).get(name, {}).get("enabled"):
            continue
        latest = db.db.execute(
            "SELECT status,finished_at,started_at FROM sync_runs WHERE source=? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()
        good = db.db.execute(
            "SELECT finished_at,total FROM sync_runs WHERE source=? AND status IN ('ok','no_new') "
            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        data = db.db.execute(
            "SELECT finished_at FROM sync_runs WHERE source=? AND status IN ('ok','no_new') AND total>0 "
            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        age = (at - datetime.fromisoformat(good["finished_at"])).total_seconds() / 3600 if good else None
        data_age = (at - datetime.fromisoformat(data[0])).total_seconds() / 3600 if data else None
        state = "never_succeeded" if not good else ("stale" if age > threshold else "healthy")
        if latest and latest["status"] not in ("ok", "no_new"):
            state = "running" if latest["status"] == "running" else "latest_failed"
            if state == "running" and (at - datetime.fromisoformat(latest["started_at"])).total_seconds() > 3600:
                state = "stuck"
        sources[name] = {
            "status": state,
            "hours_since_success": round(age, 2) if age is not None else None,
            "hours_since_data": round(data_age, 2) if data_age is not None else None,
            "acquisition_stale": data_age is None or data_age > threshold,
            "latest_status": latest["status"] if latest else None,
        }
    from core.backup import listing

    db_path = db.db.execute("PRAGMA database_list").fetchone()[2]
    try:
        backups = [r for r in listing(db_path) if r["valid"]]
        backup_age = (
            (at - max(datetime.fromisoformat(r["created_at"]) for r in backups)).total_seconds() / 3600 if backups else None
        )
        interval = max(0.01, float(cfg.get("backup", {}).get("interval_hours", 24)))
        backup_state = "missing" if backup_age is None else ("stale" if backup_age > max(24, interval * 2) else "healthy")
    except Exception:
        backup_state, backup_age = "failed", None
    collection_ok = sources and all(s["status"] == "healthy" and not s["acquisition_stale"] for s in sources.values())
    backup_ok = not cfg.get("backup", {}).get("enabled", True) or backup_state == "healthy"
    return {
        "status": "healthy" if collection_ok and backup_ok else "attention",
        "stale_hours": threshold,
        "sources": sources,
        "backup": {"status": backup_state, "hours_since_verified_backup": round(backup_age, 2) if backup_age is not None else None},
        "delivery_failures": db.db.execute("SELECT count(*) FROM deliveries WHERE status IN ('failed','unknown')").fetchone()[0],
    }
