from core.curation import assess, classify, eligible, shortlist
from core.profile import Interest, Profile


def item(**overrides):
    value = {
        "title": "普通帖子",
        "body": "普通正文",
        "channel": "科研与论文",
        "source": "byr",
        "published": "2026-10-28 10:00:00",
        "hidden": False,
        "unread": True,
        "historical": False,
        "importance": "normal",
        "deadline": {"value": None},
        "category": "general",
        "relevance": "unknown",
        "feedback": None,
    }
    value.update(overrides)
    return value


def test_staff_affairs_is_not_a_research_opportunity():
    category, protected, _ = classify(item(title="关于开展教职工体检的通知"), Profile())
    assert category == "administration"
    assert protected is False


def test_official_academic_notice_is_protected():
    category, protected, _ = classify(item(title="2026年秋季学期选课通知", channel="北邮教务处"), Profile())
    assert category == "mandatory_academic"
    assert protected is True


def test_research_recruitment_becomes_research():
    category, _, _ = classify(
        item(title="实验室招募科研助理", body="课题组招募本科生参与科研，欢迎报名。", channel="科研与论文"), Profile()
    )
    assert category == "research"


def test_incidental_mentions_do_not_reclassify():
    category, _, _ = classify(item(title="食堂新菜测评", body="今天顺便聊了聊科研和实习"), Profile())
    assert category == "general"


def test_interest_match_with_intent_is_high_relevance():
    profile = Profile(interests=[Interest(name="科研", terms=["科研"], intents=["招募"])])
    category, _, relevance, reasons = assess(
        item(title="实验室招募科研助理", body="课题组招募本科生参与科研。"), profile, None
    )
    assert category == "research"
    assert relevance == "high"
    assert any("科研" in reason["text"] for reason in reasons)


def test_graduate_only_posting_is_not_high_for_undergraduate():
    profile = Profile(stage="本科", interests=[Interest(name="科研", terms=["科研"], intents=["招募"])])
    _, _, relevance, reasons = assess(
        item(title="实验室招募科研助理", body="仅限博士研究生申请，参与科研项目。"), profile, None
    )
    assert relevance == "low"
    assert reasons


def test_eligible_requires_recent_unread_and_evidence():
    assert eligible(item(relevance="high"), current=__import__("datetime").date(2026, 10, 28))
    assert not eligible(item(relevance="unknown"), current=__import__("datetime").date(2026, 10, 28))
    assert not eligible(item(relevance="high", unread=False), current=__import__("datetime").date(2026, 10, 28))
    assert not eligible(item(relevance="high", historical=True), current=__import__("datetime").date(2026, 10, 28))


def test_eligible_drops_expired_and_old():
    import datetime

    today = datetime.date(2026, 10, 28)
    expired = item(relevance="high", deadline={"value": "2026-10-01"})
    assert not eligible(expired, current=today)
    old = item(relevance="high", published="2026-08-01 10:00:00")
    assert not eligible(old, current=today)


def test_shortlist_caps_ordinary_topics_but_keeps_important():
    important = item(importance="high", id="important")
    ordinary = [item(category="research", id=f"r{i}") for i in range(10)]
    chosen = shortlist([important] + ordinary)
    assert chosen[0] is important
    assert sum(1 for value in chosen if value.get("category") == "research") == 3
