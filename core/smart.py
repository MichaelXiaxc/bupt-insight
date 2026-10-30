"""Local priority policy and durable per-scan digests; no model-based replay."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from core.analysis import MANDATORY, current
from core.database import now


def route(item, result, status, profile, today=None):
    if MANDATORY.search(item.get("title", "") + "\n" + item.get("body", "")):
        return "immediate"
    if result is None or status != "ok" or result.confidence < 0.7:
        return "immediate"
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    if result.deadline.value:
        delta = (date.fromisoformat(result.deadline.value) - today).days
        if 0 <= delta <= 2:
            return "immediate"
    if result.importance == "high" or (result.relevance == "high" and profile.preferences.relevant == "immediate"):
        return "immediate"
    text = item.get("title", "") + "\n" + item.get("body", "")
    if profile.preferences.archive_uninterested and result.relevance == "low" and any(
        word and word in text for word in profile.uninterested
    ):
        return "archive"
    return "immediate" if profile.preferences.ordinary == "immediate" else "digest"


def message(item, result):
    reason = result.reasons[0].text if result.reasons else "个人信息不足，适用性需核对。"
    deadline = result.deadline.value or "未知，以原文为准"
    action = result.actions[0] if result.actions else "打开原文核实。"
    return item["title"][:80], (
        f"来源：{item['channel']}\n{result.summary[:110]}\n理由（推断）：{reason[:90]}\n"
        f"截止：{deadline}\n行动建议：{action[:90]}"
    )[:450]


def feedback_route(item, result, status, profile, feedback=None, today=None):
    policy = route(item, result, status, profile, today)
    if feedback is None or MANDATORY.search(item.get("title", "") + "\n" + item.get("body", "")):
        return policy
    if feedback["label"] == "important":
        return "immediate"
    if feedback["label"] == "irrelevant":
        # Preserve unknown/failed analysis and high-importance/time-sensitive items.
        if result is None or status != "ok" or result.confidence < 0.7 or result.importance == "high":
            return policy
        day = today or datetime.now(timezone(timedelta(hours=8))).date()
        if result.deadline.value and 0 <= (date.fromisoformat(result.deadline.value) - day).days <= 2:
            return policy
        return "archive"  # Explicit item/version feedback, not a global inferred preference.
    return policy


def enrich(cfg, db, row, item):
    if not cfg.get("smart", {}).get("enabled") or item.get("source") == "test":
        return "immediate", None
    notice = db.db.execute(
        "SELECT * FROM notices WHERE id=? AND revision=? AND hash=?", (row["notice_id"], row["revision"], row["hash"])
    ).fetchone()
    if notice is None:
        return "immediate", None
    from core.profile import load

    try:
        profile = load(cfg)
        result, status, engine = current(db, notice, cfg)
        from core.feedback import latest

        feedback = latest(db, notice, profile)
        policy = feedback_route(item, result, status, profile, feedback)
        enriched = message(item, result) if result and status == "ok" else None
        if enriched:
            title, body = enriched
            label = "模拟分析" if engine == "mock-rules" else ("本地规则" if engine == "rules" else "AI分析，请核实")
            enriched = (title, f"[{label}]\n" + body)
        return policy, enriched
    except Exception:
        return "immediate", None


def queue_digest(db, members, channel):
    for start in range(0, len(members), 5):
        group = members[start : start + 5]
        payload = [
            {
                "title": item["title"][:60],
                "summary": (enriched[1].split("\n")[2] if enriched and len(enriched[1].split("\n")) > 2 else item["title"])[:75],
                "url": item["url"],
            }
            for row, item, enriched in group
        ]
        with db.db:
            batch = db.db.execute(
                "INSERT INTO digest_batches(status,payload,created_at,channel) VALUES(?,?,?,?)",
                ("pending", json.dumps(payload, ensure_ascii=False), now(), channel),
            ).lastrowid
            for row, item, enriched in group:
                db.db.execute("INSERT INTO digest_members VALUES(?,?)", (row["id"], batch))
                db.db.execute("UPDATE deliveries SET status='digest_waiting' WHERE id=?", (row["id"],))


def dispatch_digests(cfg, db, sender=None):
    from core.notifications import Result, send_one

    results = []
    for batch in db.db.execute("SELECT * FROM digest_batches WHERE status IN ('pending','failed') ORDER BY id").fetchall():
        entries = json.loads(batch["payload"])
        channel = batch["channel"] or "webhook"
        title = f"BUPT Insight · {len(entries)}条普通消息汇总"
        body = "[已校验分析/本地规则摘要]\n" + "\n".join(
            f"{i+1}. {entry['summary']}" for i, entry in enumerate(entries)
        ) + "\n点击查看首条原文，其余请用 history 查看。"
        members = [r[0] for r in db.db.execute("SELECT delivery_id FROM digest_members WHERE batch_id=?", (batch["id"],))]
        with db.db:
            db.db.execute("UPDATE digest_batches SET status='sending' WHERE id=?", (batch["id"],))
        for member in members:
            db.delivery(member, "sending", attempt=True)
        try:
            result = send_one(cfg, channel, title, body[:450], entries[0]["url"], sender)
        except Exception:
            result = Result("unknown", "汇总发送状态未知，请人工核对")
        with db.db:
            db.db.execute("UPDATE digest_batches SET status=?,detail=? WHERE id=?", (result.status, result.detail, batch["id"]))
        for member in members:
            db.delivery(member, "digest_waiting" if result.status == "failed" else result.status, result.detail)
        results.append({"id": batch["id"], "channel": channel, "kind": "digest", **result.__dict__})
    return results
