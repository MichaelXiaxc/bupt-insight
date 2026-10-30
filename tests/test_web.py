import json

from core.database import identity
from core.pipeline import database
from core.web import create_app

HOST = "localhost"


def seed(cfg, notice):
    db = database(cfg)
    try:
        db.upsert(notice)
        return db.db.execute("SELECT hash FROM notices WHERE id=?", (identity(notice),)).fetchone()["hash"]
    finally:
        db.close()


def client(cfg):
    app = create_app(cfg, 8765)
    app.config["TESTING"] = True
    return app.test_client()


def test_home_renders(cfg):
    response = client(cfg).get("/", headers={"Host": HOST})
    assert response.status_code == 200
    assert "BUPT Insight" in response.get_data(as_text=True)


def test_untrusted_host_is_rejected(cfg):
    response = client(cfg).get("/", headers={"Host": "evil.example"})
    assert response.status_code == 403


def test_cross_site_request_is_rejected(cfg):
    response = client(cfg).get("/api/feed", headers={"Host": HOST, "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_feed_returns_byr_notice(cfg, notice):
    seed(cfg, notice)
    response = client(cfg).get("/api/feed?view=all", headers={"Host": HOST})
    payload = response.get_json()
    assert response.status_code == 200
    assert len(payload["items"]) == 1
    assert payload["items"][0]["title"] == notice.title
    assert payload["counts"]["all"] == 1


def test_recommended_is_empty_for_an_empty_profile(cfg, notice):
    seed(cfg, notice)
    payload = client(cfg).get("/api/feed?view=recommended", headers={"Host": HOST}).get_json()
    assert payload["items"] == []


def test_detail_returns_body(cfg, notice):
    seed(cfg, notice)
    c = client(cfg)
    item_id = c.get("/api/feed?view=all", headers={"Host": HOST}).get_json()["items"][0]["id"]
    payload = c.get(f"/api/detail?id={item_id}", headers={"Host": HOST}).get_json()
    assert "科研助理" in payload["body"]
    assert payload["url"].startswith("https://bbs.byr.cn")


def test_detail_unknown_id_is_404(cfg, notice):
    seed(cfg, notice)
    response = client(cfg).get("/api/detail?id=nope", headers={"Host": HOST})
    assert response.status_code == 404


def test_state_requires_csrf(cfg, notice):
    seed(cfg, notice)
    c = client(cfg)
    item_id = c.get("/api/feed?view=all", headers={"Host": HOST}).get_json()["items"][0]["id"]
    response = c.post("/api/state", json={"id": item_id, "hash": "x", "action": "read", "value": True}, headers={"Host": HOST})
    assert response.status_code == 403


def test_state_marks_read(cfg, notice):
    content_hash = seed(cfg, notice)
    c = client(cfg)
    item_id = c.get("/api/feed?view=all", headers={"Host": HOST}).get_json()["items"][0]["id"]
    with c.session_transaction() as session:
        session["csrf"] = "token"
    response = c.post(
        "/api/state",
        json={"id": item_id, "hash": content_hash, "action": "read", "value": True},
        headers={"Host": HOST, "X-CSRF-Token": "token"},
    )
    assert response.status_code == 200
    assert c.get("/api/feed?view=all", headers={"Host": HOST}).get_json()["items"][0]["unread"] is False


def test_stale_hash_is_conflict(cfg, notice):
    seed(cfg, notice)
    c = client(cfg)
    item_id = c.get("/api/feed?view=all", headers={"Host": HOST}).get_json()["items"][0]["id"]
    with c.session_transaction() as session:
        session["csrf"] = "token"
    response = c.post(
        "/api/state",
        json={"id": item_id, "hash": "stale", "action": "read", "value": True},
        headers={"Host": HOST, "X-CSRF-Token": "token"},
    )
    assert response.status_code == 409


def test_healthz_has_no_side_effects(cfg, notice):
    seed(cfg, notice)
    response = client(cfg).get("/healthz", headers={"Host": HOST})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_status_reports_byr(cfg, notice):
    seed(cfg, notice)
    payload = client(cfg).get("/api/status", headers={"Host": HOST}).get_json()
    assert list(payload["sources"]) == ["byr"]
    assert payload["privacy"] == "local"
