"""Evidence-first analysis with a local fallback and content/profile/policy cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.config import ROOT
from core.database import now
from core.profile import Profile

STRATEGY = "evidence-v2-interest-intent"
MANDATORY = re.compile(r"考试|补考|学籍|退学|注册|选课|必修|教务|毕业审核|毕业资格|学位审核|必须|务必|强制|安全培训|缴费")
INJECTION = re.compile(
    r"ignore.{0,40}(instruction|previous)|system\s*prompt|忽略.{0,20}(指令|规则)|泄露.{0,15}(密码|密钥)|"
    r"上传.{0,15}(cookie|密码)|<\|im_start\|>",
    re.I,
)
DATE = re.compile(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?")
DEADLINE = re.compile(r"截止|最晚|截至|之前|前提交|前报名")
SENTENCES = re.compile(r"[^。！？\n]+[。！？]?")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Claim(Strict):
    text: str = Field(max_length=350)
    basis: Literal["explicit", "inferred", "unknown"]
    evidence: str = Field(max_length=500)


class Deadline(Strict):
    value: str | None
    basis: Literal["explicit", "unknown"]
    evidence: str = Field(max_length=500)

    @model_validator(mode="after")
    def coherent(self):
        if self.basis == "unknown" and (self.value is not None or self.evidence):
            raise ValueError("unknown date has no value")
        if self.basis == "explicit":
            if not self.value or not self.evidence:
                raise ValueError("date requires evidence")
            date.fromisoformat(self.value)
        return self


class Analysis(Strict):
    category: Literal["mandatory_academic", "research", "competition", "internship", "event", "general"]
    summary: str = Field(min_length=1, max_length=240)
    key_items: list[Claim] = Field(max_length=8)
    deadline: Deadline
    eligibility: list[Claim] = Field(max_length=8)
    importance: Literal["high", "normal", "low"]
    relevance: Literal["high", "medium", "low", "unknown"]
    reasons: list[Claim] = Field(max_length=6)
    benefits: list[Claim] = Field(max_length=6)
    eligibility_match: Literal["potential", "not_met", "unknown"]
    actions: list[str] = Field(max_length=6)
    uncertainties: list[str] = Field(max_length=8)
    confidence: float = Field(ge=0, le=1)


def text_of(item) -> str:
    return (item.get("title", "") + "\n" + item.get("body", "")).strip()


def validate(value, source):
    result = Analysis.model_validate(value)
    if result.summary not in source:
        raise ValueError("summary must be an extractive source sentence")
    for claim in result.key_items + result.eligibility + result.reasons + result.benefits:
        if claim.basis == "explicit" and (not claim.evidence or claim.evidence not in source or claim.text not in claim.evidence):
            raise ValueError("ungrounded evidence")
        if claim.basis == "unknown" and claim.evidence:
            raise ValueError("unknown evidence")
    # Extraction fields must be literal source text, not a persuasive paraphrase.
    for claim in result.key_items + result.eligibility:
        if claim.basis != "unknown" and (
            claim.basis != "explicit" or claim.text not in source or claim.text not in claim.evidence
        ):
            raise ValueError("unsupported extracted fact")
    d = result.deadline
    if d.basis == "explicit":
        if d.evidence not in source or not DEADLINE.search(d.evidence):
            raise ValueError("not a deadline quote")
        dates = []
        for m in DATE.finditer(d.evidence):
            dates.append(date(*map(int, m.groups())).isoformat())
        if d.value not in dates:
            raise ValueError("invented date")
        source_deadlines = set()
        for sentence in SENTENCES.findall(source):
            if DEADLINE.search(sentence):
                for match in DATE.finditer(sentence):
                    source_deadlines.add(date(*map(int, match.groups())).isoformat())
        if source_deadlines != {d.value}:
            raise ValueError("multiple or ambiguous source deadlines")
    if MANDATORY.search(source):
        result.category, result.importance = "mandatory_academic", "high"
    # A model has no access to official eligibility checks or complete user records.
    result.eligibility_match = "unknown"
    return result


def rules(item, profile=None):
    """Conservative deterministic fallback. Not represented as model inference."""
    profile = profile or Profile()
    text = text_of(item)
    sentences = [m.group().strip() for m in SENTENCES.finditer(text) if m.group().strip()]
    category = "general"
    for label, pattern in [
        ("event", "讲座|报告会|活动"),
        ("internship", "实习|招聘"),
        ("competition", "竞赛|比赛|建模"),
        ("research", "科研|课题|研究助理"),
    ]:
        if re.search(pattern, text):
            category = label
    mandatory = bool(MANDATORY.search(text))
    if mandatory:
        category = "mandatory_academic"
    dates = []
    for sentence in sentences:
        if DEADLINE.search(sentence):
            for m in DATE.finditer(sentence):
                try:
                    dates.append((date(*map(int, m.groups())).isoformat(), sentence[:500]))
                except ValueError:
                    pass
    unique = {d for d, _ in dates}
    deadline = (
        Deadline(value=dates[0][0], basis="explicit", evidence=dates[0][1])
        if len(unique) == 1
        else Deadline(value=None, basis="unknown", evidence="")
    )
    qualification = [s for s in sentences if re.search("仅限|要求|条件|面向|资格|具备", s)]

    def explicit(s):
        return Claim(text=s[:350], basis="explicit", evidence=s[:500])

    interests = profile.research_interests + profile.skills + profile.goals + profile.opportunities
    matches = [v for v in interests if v and v in text]
    disliked = [v for v in profile.uninterested if v and v in text]
    reasons = [
        Claim(
            text="画像中关注“" + v[:70] + "”，通知包含同一词，可能相关，尚需核对具体内容。",
            basis="inferred",
            evidence="",
        )
        for v in matches[:5]
    ]
    if mandatory:
        reasons.insert(0, Claim(text="涉及教务或必须处理事项；不按兴趣过滤，需核对适用对象。", basis="inferred", evidence=""))
    unknown = ["参与资格是否完全满足尚无法确认，请核对原文和本人情况。"]
    if deadline.value is None:
        unknown.append("无法确认唯一、完整年份的截止日期；相对日期不猜测。")
    incomplete = item.get("extra", {}).get("body_status", "ok") != "ok" or not item.get("body")
    if incomplete:
        unknown.append("正文不完整或仅有附件/图片，必须打开原文查看。")
    if INJECTION.search(text):
        unknown.append("发现可能的提示注入文本；仅本地规则处理，需人工核实。")
    result = Analysis(
        category=category,
        summary=item["title"][:240],
        key_items=[explicit(s) for s in sentences[1:4]],
        deadline=deadline,
        eligibility=[explicit(s) for s in qualification[:8]],
        importance="high" if mandatory else "normal",
        relevance="high" if matches else ("low" if disliked else "unknown"),
        reasons=reasons[:6],
        benefits=[Claim(text="可能有助于画像所列目标；实际价值取决于适用条件与投入。", basis="inferred", evidence="")]
        if matches
        else [],
        eligibility_match="unknown",
        actions=["打开原帖核对适用对象、截止时间和参与方式。"],
        uncertainties=unknown,
        confidence=0.65 if incomplete or INJECTION.search(text) else 0.8,
    )
    if profile.interests:
        from core.personalization import decide

        decision = decide(item, profile)
        result.relevance = decision.relevance
        result.reasons = [
            Claim(text=f"关注“{r['interest']}”；{r['explanation']}", basis="inferred", evidence=r["evidence"])
            for r in decision.reasons[:5]
        ]
        if mandatory:
            result.reasons.insert(0, Claim(text="涉及教务或必须处理事项；不按兴趣过滤，需核对适用对象。", basis="inferred", evidence=""))
        result.benefits = (
            [Claim(text="与本人明确关注的机会方向相符；是否值得投入取决于实际条件。", basis="inferred", evidence="")]
            if decision.relevance == "high"
            else []
        )
    return validate(result.model_dump(), text)


def redact(text):
    # No URLs, credentials, contact details or long identifiers sent to models.
    text = re.sub(r"https?://\S+", "[链接已移除]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已移除]", text)
    text = re.sub(
        r"(?i)(password|密码|cookie|session|ticket|token|api[_ -]?key|authorization)\s*[:=：]\s*\S+",
        "[认证信息已移除]",
        text,
    )
    return re.sub(r"(?<!\d)\d{8,}(?!\d)", "[标识已移除]", text)


def settings(cfg):
    a = dict(cfg.get("ai", {}))
    # PyYAML's YAML 1.1 resolver treats an unquoted `off` as False.
    a["mode"] = "off" if a.get("mode") is False else a.get("mode", "off")
    if a["mode"] not in ("off", "mock", "api"):
        from core.errors import InsightError

        raise InsightError("configuration_error", "ai.mode必须为off/mock/api")
    # Environment compatibility is explicit; secrets are never put in cache keys/logs.
    a["base_url"] = os.environ.get("LLM_BASE_URL", a.get("base_url", ""))
    a["model"] = os.environ.get("LLM_MODEL", a.get("model", ""))
    return a


def is_local(base):
    from urllib.parse import urlsplit

    u = urlsplit(base)
    return (
        u.scheme in ("http", "https")
        and u.hostname in ("localhost", "127.0.0.1", "::1")
        and not u.username
        and not u.password
    )


def payload(item, profile, options):
    from core.errors import InsightError

    local = is_local(options.get("base_url", ""))
    if item.get("private") and not local and options.get("allow_private_cloud") is not True:
        raise InsightError("privacy_blocked", "私人内容未授权外部AI；使用本地规则")
    selected_profile = profile.model_dump() if local or options.get("allow_profile_cloud") is True else Profile().model_dump()
    selected_profile.pop("preferences", None)
    limit = max(500, min(16000, int(options.get("max_input_chars", 6000))))
    body = item.get("body", "")[:limit]
    return {
        "notice": {
            "title": redact(item["title"][:240]),
            "body": redact(body),
            "published": redact(item.get("published", "")[:30]),
            "source": item.get("source", "") if item.get("source") in ("byr", "test") else "other",
        },
        "profile": json.loads(redact(json.dumps(selected_profile, ensure_ascii=False))),
        "truncated": len(item.get("body", "")) > limit,
    }


def api_call(request, options, timeout):
    """Killable local worker bounds even DNS hangs or slow response streams."""
    safe_options = {k: options[k] for k in ("base_url", "model", "response_format", "retries") if k in options}
    safe_options["timeout_seconds"] = timeout
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "core.ai_worker"],
            input=json.dumps({"request": request, "options": safe_options}, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        response = json.loads(proc.stdout)
        if proc.returncode or not response.get("ok"):
            raise ValueError("model unavailable")
        return response["result"]
    except Exception:
        raise ValueError("AI request failed safely") from None


def cache_key(row, profile, options):
    defaults = {
        "mode": "off",
        "model": "",
        "base_url": "",
        "allow_private_cloud": False,
        "allow_profile_cloud": False,
        "response_format": "json_schema",
        "max_input_chars": 6000,
    }
    policy = {k: options.get(k, v) for k, v in defaults.items()}
    value = [row["id"], row["revision"], row["hash"], profile.fingerprint(), STRATEGY, policy]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def analyze_row(db, row, profile, options, remaining=10, client=None):
    item = json.loads(row["payload"])
    key = cache_key(row, profile, options)
    cached = db.db.execute("SELECT * FROM analysis_cache WHERE cache_key=?", (key,)).fetchone()
    if cached and (cached["status"] == "ok" or (cached["retry_after"] and cached["retry_after"] > now())):
        return {"status": cached["status"], "cached": True, "engine": cached["engine"]}
    result = rules(item, profile)
    engine, status = "rules", "ok"
    mode = options.get("mode", "off")
    if mode == "mock":
        engine = "mock-rules"  # Explicitly simulated, never a cloud model success.
    elif mode == "api":
        try:
            if (
                item.get("extra", {}).get("body_status", "ok") != "ok"
                or not item.get("body")
                or INJECTION.search(text_of(item))
            ):
                raise ValueError("unsafe or incomplete input")
            request = payload(item, profile, options)
            response = (client or api_call)(
                request,
                options,
                min(max(0.1, remaining), max(0.1, min(30, float(options.get("timeout_seconds", 8))))),
            )
            source = request["notice"]["title"] + "\n" + request["notice"]["body"]
            result = validate(response, source)
            if result.confidence < 0.7:
                raise ValueError("low confidence")
            # Matching against the local private profile remains local if not authorized.
            if not is_local(options.get("base_url", "")) and options.get("allow_profile_cloud") is not True:
                local_match = rules(item, profile)
                result.relevance, result.reasons, result.benefits = (
                    local_match.relevance,
                    local_match.reasons,
                    local_match.benefits,
                )
                result.eligibility_match = "unknown"
            engine = "api"
        except Exception:
            result = rules(item, profile)
            engine, status = "rules-fallback", "fallback"
    retry_after = (
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds") if status == "fallback" else None
    )
    with db.db:
        db.db.execute(
            """INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key,
                row["id"],
                row["revision"],
                row["hash"],
                profile.fingerprint(),
                STRATEGY,
                engine,
                status,
                result.model_dump_json(),
                now(),
                retry_after,
            ),
        )
    return {"status": status, "cached": False, "engine": engine}


def ensure_llm_env(cfg) -> None:
    """Expose the encrypted API key to the worker subprocess, never to logs."""
    if os.environ.get("LLM_API_KEY"):
        return
    try:
        from core.secrets import load_llm_key

        value = load_llm_key(cfg)
    except Exception:
        value = None
    if value:
        os.environ["LLM_API_KEY"] = value


def run_pending(cfg, db, limit=None, client=None):
    options = settings(cfg)
    if options.get("mode", "off") == "off" and not cfg.get("smart", {}).get("enabled", False):
        return {"status": "disabled", "processed": 0}
    ensure_llm_env(cfg)
    from core.profile import load

    try:
        profile = load(cfg)
    except Exception:
        return {"status": "profile_invalid", "processed": 0}
    start = time.monotonic()
    budget = max(0.1, min(60, float(options.get("budget_seconds", 12))))
    count = max(1, min(100, int(limit or options.get("max_per_run", 20))))
    # New pending reminders first. Never backfill old baseline automatically.
    rows = db.db.execute(
        """SELECT DISTINCT n.* FROM notices n JOIN deliveries d ON d.notice_id=n.id
      AND d.revision=n.revision WHERE d.status IN ('pending','failed') ORDER BY n.updated_at DESC LIMIT ?""",
        (count,),
    ).fetchall()
    reports = []
    for row in rows:
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0.1:
            break
        try:
            reports.append(analyze_row(db, row, profile, options, remaining, client))
        except Exception:
            reports.append({"status": "failed", "engine": "none"})
    return {"status": "completed", "processed": len(reports), "results": reports}


def current(db, row, cfg):
    from core.profile import load

    try:
        profile = load(cfg)
        key = cache_key(row, profile, settings(cfg))
        value = db.db.execute("SELECT * FROM analysis_cache WHERE cache_key=?", (key,)).fetchone()
        if value:
            return Analysis.model_validate_json(value["result"]), value["status"], value["engine"]
    except Exception:
        pass
    return None, "missing", "none"
