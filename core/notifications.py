"""Outbox delivery. "Accepted by the provider" and "received by the human" differ.

Channels are plain HTTP so the same code runs on a Linux server:

* ``webhook`` — generic JSON POST, useful for Home Assistant / n8n / bots;
* ``ntfy`` — phone push, self-hostable;
* ``pushplus`` / ``serverchan`` — WeChat gateways (optional).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import requests

CHANNELS = ("webhook", "ntfy", "pushplus", "serverchan")


@dataclass
class Result:
    status: str
    detail: str
    receipt: str | None = None


def message(item, external=True):
    title = item["title"][:80]
    if item.get("private") and external:
        # A local summary can still contain private details; no automatic disclosure.
        body = f"来源：{item['channel']}\n发布时间：{item.get('published', '未知')}\n论坛有一条新帖或正文更新，请在本地阅读页查看。"
    else:
        excerpt = item.get("body", "")[:450]
        if item.get("extra", {}).get("body_status") == "attachment_only":
            excerpt = "本帖以附件提供正文，请点击原文查看；附件内容尚未提取。"
        elif item.get("extra", {}).get("body_status") == "image_only":
            excerpt = "本帖以图片提供正文，请点击原文查看；图片文字尚未识别。"
        body = f"来源：{item['channel']}\n{item.get('published', '')}\n{excerpt}\n{item['url']}"
    return title, body[:1200]


def webhook(channel, title, body, url="", post=None):
    endpoint = str(channel.get("url") or "").strip()
    if not endpoint.startswith(("https://", "http://")):
        return Result("failed", "webhook 地址为空或不是 http(s) URL")
    if any(hint in endpoint for hint in ("在这里填", "your-webhook", "xxxx", "<", ">")):
        return Result("failed", "webhook 仍是模板占位符")
    payload = {"title": title[:120], "body": body[:1200], "url": url, "source": "bupt-insight"}
    try:
        response = (post or requests.post)(endpoint, json=payload, timeout=(6, 15), allow_redirects=False)
    except requests.ConnectTimeout:
        return Result("failed", "连接超时，未发送，可重试")
    except (requests.RequestException, ValueError):
        return Result("unknown", "发送结果不确定；避免自动重发，请人工核对")
    if response.status_code >= 500:
        return Result("unknown", f"webhook 服务端异常（HTTP {response.status_code}）")
    if response.status_code >= 400:
        return Result("failed", f"webhook 拒绝（HTTP {response.status_code}）")
    return Result("accepted", "webhook 已受理，接收端是否显示需自行确认")


def ntfy(channel, title, body, url="", post=None):
    server = str(channel.get("server") or "https://ntfy.sh").rstrip("/")
    topic = str(channel.get("topic") or "").strip()
    if not topic:
        return Result("failed", "ntfy 缺少 topic")
    if not server.startswith(("https://", "http://")):
        return Result("failed", "ntfy server 不是 http(s) URL")
    # JSON publishing keeps UTF-8 titles intact; HTTP headers are latin-1 only.
    payload = {"topic": topic, "title": title[:100], "message": body[:1000], "priority": 3}
    if url:
        payload["click"] = url
    headers = {"Content-Type": "application/json; charset=utf-8"}
    token = str(channel.get("token") or os.environ.get("NTFY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = (post or requests.post)(server, json=payload, headers=headers, timeout=(6, 15))
    except requests.ConnectTimeout:
        return Result("failed", "连接超时，未发送，可重试")
    except (requests.RequestException, ValueError):
        return Result("unknown", "发送结果不确定；避免自动重发，请人工核对")
    if response.status_code >= 500:
        return Result("unknown", f"ntfy 服务端异常（HTTP {response.status_code}）")
    if response.status_code >= 400:
        return Result("failed", f"ntfy 拒绝（HTTP {response.status_code}）")
    return Result("accepted", "ntfy 已受理，手机是否收到需自行确认")


def wechat(kind, title, body, token=None, post=None):
    token = token or os.environ.get("PUSHPLUS_TOKEN" if kind == "pushplus" else "SERVERCHAN_TOKEN")
    if not token:
        return Result("failed", "缺少推送密钥；请在 config.yaml 的 push.channels 中填写 token")
    post = post or requests.post
    if kind == "pushplus":
        url = "https://www.pushplus.plus/send"
        args = {"json": {"token": token, "title": title[:80], "content": body[:1200], "template": "txt", "channel": "wechat"}}
    elif kind == "serverchan":
        if not token.startswith("SCT") or not token.isalnum():
            return Result("failed", "当前适配器仅支持 Turbo SCT SendKey")
        url = f"sctapi.ftqq.com/{token}.send"
        args = {"data": {"title": title[:80], "desp": body[:1200]}}
    else:
        return Result("failed", "未知微信渠道")
    try:
        response = post(url, timeout=(8, 20), allow_redirects=False, **args)
        if response.status_code >= 500:
            return Result("unknown", "服务端异常，是否受理未知；请核对后再决定重试")
        if response.status_code != 200:
            return Result("failed", f"推送接口 HTTP {response.status_code}")
        data = response.json()
        code = data.get("code")
        ok = (kind == "pushplus" and code == 200) or (kind == "serverchan" and code == 0)
        if not ok:
            # Do not persist server message: it can echo token, URL or payload.
            return Result("failed", "推送服务业务拒绝（请在服务官网核对密钥、配额和订阅状态）")
        receipt = data.get("data")
        if isinstance(receipt, dict):
            receipt = receipt.get("pushid")
        if not isinstance(receipt, (str, int)):
            receipt = None
        return Result("accepted", "服务端已受理，微信接收尚未确认", str(receipt) if receipt else None)
    except requests.ConnectTimeout:
        return Result("failed", "连接超时，未发送正文，可重试")
    except (requests.RequestException, ValueError, TypeError):
        return Result("unknown", "发送结果不确定；避免自动重发，请在微信和服务后台核对")


def check_receipt(receipt, access_key=None, get=None):
    """Official read-only PushPlus receipt API; status=2 is provider delivery, not a read receipt."""
    access_key = access_key or os.environ.get("PUSHPLUS_ACCESS_KEY")
    if not access_key:
        return Result("accepted", "缺少 PushPlus AccessKey；可在微信确认收到后执行 delivery confirm")
    try:
        response = (get or requests.get)(
            "https://www.pushplus.plus/api/open/message/sendMessageResult",
            params={"shortCode": receipt},
            headers={"access-key": access_key},
            timeout=(8, 20),
            allow_redirects=False,
        )
        payload = response.json()
        if response.status_code != 200 or payload.get("code") != 200:
            return Result("accepted", "回执查询失败，保留已受理状态")
        status = payload.get("data", {}).get("status")
        return Result(
            {0: "accepted", 1: "accepted", 2: "provider_delivered", 3: "failed"}.get(status, "accepted"),
            "官方回执已查询；provider_delivered 表示服务报告已发送，仍不代表用户阅读",
            receipt,
        )
    except Exception:
        return Result("accepted", "回执查询网络失败，保留已受理状态")


def enabled(cfg) -> list[str]:
    channels = cfg.get("push", {}).get("channels", {}) or {}
    return [name for name in CHANNELS if isinstance(channels.get(name), dict) and channels[name].get("enabled")]


def send_one(cfg, channel, title, body, url="", sender=None) -> Result:
    if sender:
        return sender(channel, title, body, url)
    channel_cfg = (cfg.get("push", {}).get("channels", {}) or {}).get(channel, {}) or {}
    if channel == "webhook":
        return webhook(channel_cfg, title, body, url)
    if channel == "ntfy":
        return ntfy(channel_cfg, title, body, url)
    if channel in ("pushplus", "serverchan"):
        return wechat(channel, title, body, token=channel_cfg.get("token"))
    return Result("failed", f"未知渠道 {channel}")


def dispatch(cfg, db, sender=None, notice_id=None):
    channels = enabled(cfg)
    results = []
    digest_members: dict[str, list] = {}
    for row in db.pending(channels):
        if notice_id and row["notice_id"] != notice_id:
            continue
        item = json.loads(row["payload"])
        channel = row["channel"]
        title, body = message(item, external=channel != "webhook")
        try:
            from core.smart import enrich

            policy, enriched = enrich(cfg, db, row, item)
        except Exception:
            policy, enriched = "immediate", None
        if policy == "archive":
            db.delivery(row["id"], "archived", "匹配本人不感兴趣项；已存本地历史")
            results.append({"id": row["id"], "channel": channel, "status": "archived"})
            continue
        if policy == "digest" and notice_id is None:
            digest_members.setdefault(channel, []).append((row, item, enriched))
            continue
        if enriched:
            title, body = enriched
        result = Result("failed", "未尝试")
        for attempt in range(max(1, cfg.get("push", {}).get("retries", 3))):
            db.delivery(row["id"], "sending", attempt=True)
            try:
                result = send_one(cfg, channel, title, body, item["url"], sender)
            except Exception:
                result = Result("unknown", "发送器异常，结果未知，需人工核对")
            db.delivery(row["id"], result.status, result.detail, result.receipt)
            if result.status != "failed":
                break
            if attempt + 1 < cfg.get("push", {}).get("retries", 3):
                time.sleep(min(2**attempt, 8))
        results.append({"id": row["id"], "channel": channel, **result.__dict__})

    if digest_members:
        from core.smart import queue_digest

        for channel, members in digest_members.items():
            queue_digest(db, members, channel)
    if notice_id is None:
        from core.smart import dispatch_digests

        results.extend(dispatch_digests(cfg, db, sender))
    return results
