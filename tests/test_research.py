import datetime

from core.research import opportunity


def make(title, body, published="2026-10-20 10:00:00"):
    return {"title": title, "body": body, "published": published}


def test_undergraduate_recruitment_is_a_candidate():
    value = opportunity(make("实验室招募科研助理", "课题组招募本科生参与科研，欢迎大二大三同学报名。"))
    assert value["candidate"] is True
    assert value["undergraduate"]
    assert value["priority"] >= 2


def test_graduate_only_posting_is_excluded():
    value = opportunity(make("课题组招募科研助理", "本岗位仅限博士研究生申请。"))
    assert value["candidate"] is False
    assert value["eligibility"] == "graduate_only"


def test_closed_recruitment_is_excluded():
    value = opportunity(make("实验室招募科研助理", "招募已结束，名额已满。"))
    assert value["candidate"] is False
    assert value["exclusion"]


def test_seeker_post_is_excluded():
    value = opportunity(make("求科研导师带带", "本人想找导师指导科研。"))
    assert value["candidate"] is False


def test_relative_deadline_is_inferred_from_published_year():
    value = opportunity(make("实验室招募科研助理", "欢迎本科生参与科研，报名截止 11 月 20 日。", "2026-10-01 09:00:00"))
    assert value["deadline"] == "2026-11-20"
    assert value["deadline_inferred"] is True


def test_past_deadline_marks_exclusion():
    value = opportunity(make("实验室招募科研助理", "欢迎本科生参与科研，截止 2026-01-10。", "2026-10-01 09:00:00"))
    assert value["candidate"] is False


def test_authorship_evidence_raises_priority():
    plain = opportunity(make("实验室招募科研助理", "欢迎本科生参与科研。"))
    richer = opportunity(
        make("实验室招募科研助理", "欢迎本科生参与科研。已有论文初稿，按贡献提供二作署名。")
    )
    assert richer["authorship"]
    assert richer["priority"] > plain["priority"]


def test_mentor_quote_is_extracted():
    value = opportunity(make("实验室招募科研助理", "合作导师：张老师，主要研究方向为具身智能。欢迎本科生参与科研。"))
    assert "张老师" in value["mentor"]
