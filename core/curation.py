"""Reader-only precision policy. Never changes collection or delivery records."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

VERSION = "reader-focus-v4-byr"
TOPICS = {
    "internship": r"实习|招聘|内推|兼职|校招",
    "competition": r"竞赛|比赛|建模|挑战赛|电赛|美赛",
    "research": r"科研|课题|研究助理|科研助理|实验室|\bRA\b",
    "event": r"讲座|报告会|活动|讲堂|社团|工作坊",
    "electronics": r"电子设计|电赛|科创|硬件|嵌入式|FPGA|单片机|电路设计",
}
STAFF = re.compile(r"夫妻两地分居|职称|职务聘任|岗位聘任|干部任免|教职工体检|教师资格认定|事业编制|教职工住房")
STAFF_AUDIENCE = re.compile(r"(?:面向|仅限|对象[为是：:])[^。\n]{0,25}(?:在编教职工|事业编制人员|在职教师|专任教师)")
ACADEMIC = re.compile(r"考试|补考|学籍|退学|选课|必修|教务|毕业审核|毕业资格|学位审核|学生注册|学费缴纳")
OFFICIAL_BOARD = re.compile(r"教务处|学生处|研究生院|毕业生")
REVIEW = re.compile(r"回顾|圆满结束|成功举办|获奖名单|结果公示|拟录用|拟聘|成交|采购|招标|报销")
OPPORTUNITY = re.compile(r"招募|招收|招聘|报名|申请|征集|招新|组队|找队友|开放|招生|宣讲|讲座|报告会|竞赛|比赛|挑战赛|活动|工作坊|实习")


def today(cfg=None):
    zone = timezone(timedelta(hours=8))
    if cfg:
        from core.config import timezone as configured

        try:
            zone = configured(cfg)
        except Exception:
            pass
    return datetime.now(zone).date()


def classify(item, profile, cfg=None):
    """The subject takes precedence over incidental words in a long body."""
    title = item.get("title", "")
    body = item.get("body", "")
    channel = item.get("channel", "")
    staff = bool(STAFF.search(title) or STAFF_AUDIENCE.search(body[:1200]))
    if staff:
        return "administration", False, "通知明确涉及教职工或人事事务，未发现面向学生的参与机会。"
    academic = bool(ACADEMIC.search(title) and (OFFICIAL_BOARD.search(channel) or re.search("通知|安排|核对|办理", title)))
    if academic:
        return "mandatory_academic", True, "标题涉及学籍、考试或教务办理；请核对适用年级与办理要求。"
    from core.research import opportunity

    research = opportunity(item, today(cfg))
    if research["candidate"]:
        return "research", False, ""
    for category in ("competition", "internship", "electronics", "research", "event"):
        if re.search(TOPICS[category], title, re.I):
            return category, False, ""
    # No whole-body keyword category: incidental research/employment mentions are
    # not evidence that the subject is an opportunity.
    return "general", False, ""


def assess(item, profile, analysis, cfg=None):
    category, protected, exclusion = classify(item, profile, cfg)
    title = item.get("title", "")
    body = item.get("body", "")
    reasons = []
    relevance = "unknown"
    if protected:
        reasons = [{"text": exclusion, "basis": "inferred", "evidence": title}]
    elif exclusion:
        relevance = "low"
        reasons = [{"text": exclusion, "basis": "inferred", "evidence": title}]
    elif category in TOPICS:
        from core.research import opportunity

        research = opportunity(item, today(cfg))
        undergraduate = bool(re.search("本科|大[一二三四]", profile.stage + profile.grade))
        if research["exclusion"] and (category == "research" or undergraduate):
            return category, False, "low", [
                {
                    "text": "科研招募存在资格限制、已截止或属于求助信息，暂不推荐。",
                    "basis": "inferred",
                    "evidence": research["exclusion"],
                }
            ]
        graduate_only = re.search(
            r"博士后(?:招聘|招收)|(?:仅限|仅面向|必须为|须为)[^。\n]{0,12}(?:硕士研究生|博士研究生|在读博士)",
            title + "\n" + body[:1200],
        )
        if undergraduate and graduate_only:
            return category, False, "low", [
                {
                    "text": "原文明确限定研究生或博士后，与当前本科阶段不符，不进入精选。",
                    "basis": "inferred",
                    "evidence": graduate_only.group(),
                }
            ]
        # Match the opportunity type against the user's explicit interests, not
        # generic skills buried in an unrelated notice.
        interests = [(i.name, " ".join([i.name, *i.terms])) for i in profile.interests]
        if not interests:
            interests = [(v, v) for v in profile.opportunities + profile.research_interests + profile.goals]
        matched = [name for name, terms in interests if re.search(TOPICS[category], terms, re.I)]
        actionable = OPPORTUNITY.search(title + "\n" + body[:600]) and not REVIEW.search(title)
        decision_reasons = analysis.get("reasons", []) if analysis else []
        conflicts = any(re.search(re.escape(v), title + "\n" + body[:1200], re.I) for v in profile.uninterested if v)
        if matched and actionable and not conflicts:
            relevance = "high"
            reasons = [
                {
                    "text": f"你关注“{matched[0]}”；标题明确涉及该类机会。参与资格仍需核对原文。",
                    "basis": "inferred",
                    "evidence": title,
                }
            ]
            if research and research["candidate"]:
                detail = (
                    "原文提及本科生参与，具体招募条件与申请方式请查看详情。"
                    if research["undergraduate"]
                    else "科研招募信息，参与条件与申请方式请查看原文。"
                )
                reasons.insert(0, {"text": detail, "basis": "inferred", "evidence": research["undergraduate"] or title})
        elif matched:
            relevance = "medium"
            reasons = [
                {
                    "text": "主题与你的兴趣接近，但尚未确认可参与的机会，不进入每日精选。",
                    "basis": "inferred",
                    "evidence": title,
                }
            ]
        # Preserve a direction-specific explanation only after the subject gate.
        if relevance == "high" and decision_reasons:
            reasons += [r for r in decision_reasons if r.get("evidence") and r["evidence"] in title][:1]
    return category, protected, relevance, reasons


def eligible(item, current=None):
    """Short daily reading list: current, unread, with affirmative evidence."""
    current = current or today()
    if item["hidden"] or not item["unread"] or item["historical"]:
        return False
    try:
        age = (current - date.fromisoformat(item["published"][:10])).days
    except (TypeError, ValueError):
        return False  # Unknown age remains searchable, never presented as fresh.
    if not 0 <= age <= 30:
        return False
    if item["importance"] == "high":
        return True
    deadline = item["deadline"].get("value")
    if deadline and date.fromisoformat(deadline) < current:
        return False
    if item["category"] == "administration":
        return False
    return item["relevance"] == "high" or item["feedback"] in ("relevant", "important")


def shortlist(items):
    """Keep important academic items; ordinary topics can't flood the front page."""
    important = [i for i in items if i["importance"] == "high"]
    chosen = []
    topics = {}
    for item in items:
        if item["importance"] == "high":
            continue
        category = item["category"]
        if topics.get(category, 0) >= 3:
            continue
        chosen.append(item)
        topics[category] = topics.get(category, 0) + 1
        if len(chosen) == 8:
            break
    return important + chosen
