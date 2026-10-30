"""Loopback reader. Collection data is read-only; UI state lives in a sidecar DB."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets as random_secrets
import sqlite3
import threading
from datetime import date, datetime
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, session

from core.config import ROOT, allowed_hosts, cookie_secure, load_config
from core.errors import InsightError
from core.pipeline import database_path
from core.profile import load as load_profile
from core.reader import Reader, feedbacks, get_rows, run_status, source
from core.secrets import session_secret

log = logging.getLogger(__name__)

CATEGORIES = {
    "research": "科研机会",
    "competition": "竞赛",
    "internship": "实习就业",
    "event": "校园活动",
    "mandatory_academic": "教务通知",
    "general": "校园资讯",
    "electronics": "电子科创",
    "administration": "校务人事",
}


def safe_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def create_app(config=None, port: int | None = None):
    cfg = config or load_config()
    port = int(port or cfg.get("server", {}).get("port", 8000))
    hosts = allowed_hosts(cfg, port)

    app = Flask(__name__, template_folder=str(ROOT / "web/templates"), static_folder=str(ROOT / "web/static"))
    app.config.update(
        SECRET_KEY=session_secret(cfg),
        MAX_CONTENT_LENGTH=8192,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=cookie_secure(cfg),
    )
    app.logger.disabled = True
    readers: dict[str, Reader] = {}
    reader_lock = threading.RLock()
    cache: dict = {}
    cache_lock = threading.RLock()

    def context():
        path = database_path(cfg)
        with reader_lock:
            if str(path) not in readers:
                readers[str(path)] = Reader(path.parent / "reader.sqlite3")
            reader = readers[str(path)]
        return cfg, path, reader

    @app.before_request
    def protect():
        # Host validation blocks DNS rebinding; no CORS, no remote assets.
        if request.host.lower() not in hosts:
            # Log the rejected name (never the path) so a misconfigured domain is
            # diagnosable from journalctl instead of being a bare 403.
            log.warning(
                "rejected Host header %r; allowed: %s",
                request.host[:120],
                ",".join(sorted(hosts)),
            )
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or (parsed.netloc or "").lower() not in hosts:
                abort(403)
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not random_secrets.compare_digest(token, session.get("csrf", "")):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.errorhandler(InsightError)
    def insight(exc):
        return jsonify(error=exc.status, message=str(exc)), 503

    @app.errorhandler(sqlite3.Error)
    def storage(exc):
        log.exception("sqlite error")
        return jsonify(error="storage_unavailable", message="本地数据暂时不可用，请稍后重试；原文件已保留。"), 503

    @app.errorhandler(403)
    def forbidden(exc):
        return jsonify(error="forbidden", message="请从本机页面操作或刷新页面后重试。"), 403

    @app.errorhandler(Exception)
    def safe_failure(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(error="request_failed", message="请求无效或内容不存在。"), exc.code
        log.exception("unhandled request error")
        return jsonify(error="unavailable", message="页面暂时无法读取数据，请检查本地配置和运行状态。"), 503

    @app.get("/")
    def home():
        session["csrf"] = random_secrets.token_hex(24)
        return render_template("index.html", csrf=session["csrf"], site_title=cfg["site"].get("title", "BUPT Insight"))

    def decorate(row, profile, states, events):
        from core.analysis import rules
        from core.curation import assess, today
        from core.research import opportunity

        item = json.loads(row["payload"])
        key = (row["id"], row["hash"], profile.fingerprint())
        with cache_lock:
            if key not in cache:
                if len(cache) > 2000:
                    cache.clear()
                try:
                    cache[key] = rules(item, profile).model_dump()
                except Exception:
                    cache[key] = None
            analysis = cache[key]
        state = states.get(row["id"], {})
        feedback = events.get(key)
        category, protected, relevance, reasons = assess(item, profile, analysis, cfg)
        research = opportunity(item, today(cfg))
        hidden = state.get("hidden_hash") == row["hash"] or bool(feedback and feedback["label"] == "irrelevant")
        if protected:
            hidden = False  # Safety-protected academic items remain visible.
        published = _as_date(row["published"])
        historical = bool(published and (today(cfg) - published).days > 180)
        body = row["body"] or ""
        summary = body.split("\n\n", 1)[1] if body.startswith("发信人:") and "\n\n" in body else body
        return {
            "id": row["id"],
            "hash": row["hash"],
            "title": row["title"],
            "source": row["source"],
            "origin": row["origin"],
            "published": row["published"],
            "first_seen": row["first_seen"],
            "updated_at": row.get("content_seen", row["updated_at"]) if row["revision"] > 1 else row["first_seen"],
            "revision": row["revision"],
            "url": safe_url(row["url"]),
            "summary": (summary or "正文暂不可用，请打开原文查看。")[:240],
            "category": category,
            "category_label": CATEGORIES.get(category, CATEGORIES["general"]),
            "relevance": relevance,
            "research": research,
            "importance": "high" if protected else "normal",
            "historical": historical,
            "deadline": analysis["deadline"] if analysis else {"value": None, "basis": "unknown", "evidence": ""},
            "reasons": reasons[:3],
            "body_status": item.get("extra", {}).get("body_status", "ok"),
            "unread": state.get("read_hash") != row["hash"],
            "favorite": bool(state.get("favorite")),
            "hidden": hidden,
            "feedback": feedback["label"] if feedback else None,
            "feedback_pending": bool(feedback and not feedback["synced"]),
        }

    def all_feedback(path, reader, profile):
        merged = feedbacks(path, profile.fingerprint())
        for key, event in reader.events().items():
            if key not in merged or event["created_at"] >= merged[key]["created_at"]:
                merged[key] = event
        return merged

    @app.get("/api/feed")
    def feed():
        _, path, reader = context()
        profile = load_profile(cfg)
        states = reader.states()
        events = all_feedback(path, reader, profile)
        rows = get_rows(path)
        items = [decorate(r, profile, states, events) for r in rows]
        counts = {
            "all": len(items),
            "unread": sum(i["unread"] for i in items),
            "favorites": sum(i["favorite"] for i in items),
            "hidden": sum(i["hidden"] for i in items),
        }
        view = request.args.get("view", "recommended")
        query = request.args.get("q", "").strip().casefold()[:200]
        category = request.args.get("category", "")
        source_filter = request.args.get("source", "")
        filtered = []
        for item, raw in zip(items, rows):
            if view == "hidden" and not item["hidden"]:
                continue
            if view not in ("hidden", "all", "favorites") and item["hidden"]:
                continue
            if view == "favorites" and not item["favorite"]:
                continue
            if view == "unread" and not item["unread"]:
                continue
            if view == "research":
                from core.curation import today

                published = _as_date(item["published"])
                if published is None:
                    continue
                age = (today(cfg) - published).days
                if not 0 <= age <= 90 or not item["research"]["candidate"]:
                    continue
            if view == "recommended":
                from core.curation import eligible, today as curation_today

                if not eligible(item, curation_today(cfg)):
                    continue
            if category and item["category"] != category:
                continue
            if source_filter and item["source"] != source_filter:
                continue
            if query and query not in (item["title"] + "\n" + item["origin"] + "\n" + raw["body"]).casefold():
                continue
            filtered.append(item)
        sort = request.args.get("sort", "recommended" if view == "recommended" else "published")

        def rank(i):
            priority = 2 if i["importance"] == "high" or i["feedback"] == "important" else (1 if i["relevance"] == "high" else 0)
            return (int(not i["historical"]), int(i["unread"]), priority, i["research"]["priority"], i["published"], i["updated_at"])

        if sort == "recommended":
            filtered.sort(key=rank, reverse=True)
        elif sort == "collected":
            filtered.sort(key=lambda i: i["updated_at"], reverse=True)
        else:
            filtered.sort(key=lambda i: (i["published"], i["updated_at"]), reverse=True)
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            abort(400)
        eligible_total = len(filtered)
        if view == "recommended":
            # Cap ordinary recommendations, not compulsory academic matters.
            from core.curation import shortlist

            filtered = shortlist(filtered)
            page = 1
        size = max(8, len(filtered)) if view == "recommended" else 20
        start = (page - 1) * size
        return jsonify(
            items=filtered[start : start + size],
            total=len(filtered),
            page=page,
            page_size=size,
            counts=counts,
            categories=CATEGORIES,
            eligible_total=eligible_total,
            version=hashlib.sha256(
                json.dumps([(i["id"], i["hash"]) for i in items], sort_keys=True).encode()
            ).hexdigest(),
        )

    @app.get("/api/detail")
    def detail():
        cfg_value, path, reader = context()
        with source(path) as db:
            raw = db.execute("SELECT * FROM notices WHERE id=?", (request.args.get("id"),)).fetchone()
            row = dict(raw) if raw else None
        if not row:
            abort(404)
        item = json.loads(row["payload"])
        profile = load_profile(cfg_value)
        result = decorate(row, profile, reader.states(), all_feedback(path, reader, profile))
        result["body"] = row["body"] or "正文暂不可用，请打开原文查看。"
        result["attachments"] = [
            {"name": a.get("name", "附件"), "url": safe_url(a.get("url", ""))}
            for a in item.get("extra", {}).get("attachments", [])
        ]
        return jsonify(result)

    @app.post("/api/state")
    def state():
        body = request.get_json(silent=True) or {}
        if body.get("action") not in ("read", "favorite", "hidden", "feedback"):
            abort(400)
        if body["action"] == "feedback":
            if body.get("value") not in ("important", "relevant", "irrelevant", "unsure"):
                abort(400)
        elif type(body.get("value")) is not bool:
            abort(400)
        cfg_value, path, reader = context()
        with source(path) as db:
            raw = db.execute("SELECT * FROM notices WHERE id=?", (body.get("id"),)).fetchone()
            row = dict(raw) if raw else None
        if not row:
            abort(404)
        if body.get("hash") != row["hash"]:
            return jsonify(error="content_changed", message="正文已有更新，请刷新后操作，避免对旧版本误标记。"), 409
        reader.mutate(row, body["action"], body["value"], load_profile(cfg_value))
        reader.flush(cfg_value, path)
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        _, path, reader = context()
        reader.flush(cfg, path)
        return jsonify(
            sources=run_status(cfg, path),
            privacy="local",
            model="off_in_reader",
            feedback_pending=sum(not e["synced"] for e in reader.events().values()),
        )

    @app.get("/healthz")
    def healthz():
        """Cheap liveness/readiness probe: no side effects, no lock acquisition."""
        path = database_path(cfg)
        try:
            with source(path) as db:
                db.execute("SELECT 1").fetchone()
                row = db.execute(
                    "SELECT finished_at,status FROM sync_runs WHERE source='byr' AND status IN ('ok','no_new') "
                    "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except Exception:
            return jsonify(status="unavailable"), 503
        hours = None
        if row and row["finished_at"]:
            hours = round((datetime.now().astimezone() - datetime.fromisoformat(row["finished_at"])).total_seconds() / 3600, 2)
        return jsonify(status="ok", hours_since_success=hours)

    @app.get("/api/identity")
    def identity():
        return jsonify(app="BUPTInsightReader", protocol=2)

    return app


def serve(host=None, port=None, open_browser=False):
    cfg = load_config()
    host = host or cfg.get("server", {}).get("host", "127.0.0.1")
    port = int(port or cfg.get("server", {}).get("port", 8000))
    if not 1024 <= port <= 65535:
        raise InsightError("configuration_error", "本地端口应在1024至65535之间")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise InsightError("configuration_error", "为避免直接暴露，内置服务只监听回环地址；对外请用 nginx 反代")
    app = create_app(cfg, port)
    from waitress import serve as waitress_serve

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"BUPT Insight：http://127.0.0.1:{port} 关闭服务请按 Ctrl+C", flush=True)
    waitress_serve(app, host=host, port=port, threads=4, channel_timeout=20)
