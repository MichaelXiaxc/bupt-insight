"""Evidence-based recruitment signals for undergraduate research opportunities."""
import re
from datetime import date


RESEARCH = re.compile(r'科研|课题|实验室|研究助理|研究团队|论文|研究院|研究所|\bRA\b|research\s+(?:assistant|intern)', re.I)
RECRUIT = re.compile(r'招募|招收|招聘|招新|诚招|招人|找合作者|寻人|招.{0,5}(?:实习|本科)|寻求.{0,8}合作|寻找.{0,8}(?:合作者|队友|同学)|缺人|需要人手|补实验|补充实验|research\s+assistant', re.I)
CONTEXT = re.compile(r'科研实习|科研指导|研究助理|参与[^。\n]{0,20}科研|合作[^。\n]{0,20}论文|(?:顶会|论文)[^。\n]{0,20}(?:投稿|产出)|research\s+(?:assistant|intern)', re.I)
DIRECT_RECRUIT = re.compile(r'(?:教授|老师|导师|课题组|实验室|研究团队)[^。\n]{0,45}(?:招募|招收|招聘|招新)')
UNDERGRADUATE = re.compile(r'本科(?:生|及以上|高年级)|(?:在读|在校)本科|大[一二三四](?:[、，,和及与\s]*大?[一二三四])?(?:年级)?(?:学生|同学)|(?:面向|欢迎|招收|招募|招聘|接受|学历要求)[^。\n]{0,18}(?:本科|大[一二三四])|大[一二三四](?:年级)?[^。\n]{0,12}(?:优先|可报名|可申请|可参加)')
GRADUATE = re.compile(r'博士后(?:招聘|招收)|(?:仅限|仅面向|必须为|须为|面向|招收|招聘)[^。\n]{0,16}(?:硕士研究生|博士研究生|在读博士|硕士生|博士生)|(?:学历(?:要求)?[：:\s]*)?(?:硕士[、/及和\s]*博士|硕士研究生|博士研究生)[^。\n]{0,12}(?:在读|学历|可投递)|(?:硕士|博士)(?:研究生)?(?:及以上|起步)|在读硕士或博士研究生')
NO_UNDERGRADUATE = re.compile(r'(?:不招|不接受|不面向)[^。\n]{0,8}(?:本科|大[一二三四])')
SEEKER = re.compile(r'求.{0,8}(?:科研|导师|指导|实验室)|寻找.{0,8}(?:导师|科研机会)|论文代写|付费署名|有偿挂名|科研培训|有偿指导')
CLOSED = re.compile(r'已招满|招募结束|招聘结束|停止招募|停止招聘|暂停招募|名额已满|已截止')
ONGOING = re.compile(r'尚未投稿|还未投稿|还没投稿|未投稿|投稿前|论文[^。\n]{0,12}还没投(?!递)|在研(?:项目|课题)?|已有.{0,12}(?:初稿|结果)|补(?:充)?实验|(?:项目|课题|论文).{0,8}(?:进行中|已启动|已开展)|(?:数据|任务定义|论文框架)[^。\n]{0,25}(?:已经具备|已完成|已准备)|正在推进[^。\n]{0,25}研究')
AUTHORSHIP = re.compile(r'(?:提供|给予|可获|可以|支持|安排|保证|承诺|共同|合作|按贡献|根据贡献|贡献决定)[^。\n]{0,35}(?:二作|三作|第二作者|第三作者|共同作者|联合作者|署名)|(?:二作|三作|第二作者|第三作者|共同作者|联合作者|署名)[^。\n]{0,30}(?:机会|名额|贡献|协商|商议|分配|可谈)|(?:二作|三作|第二作者|第三作者)(?:[、，/或和及\s]*(?:二作|三作|第二作者|第三作者))*[：:]')
BENEFIT_AUTHORSHIP = re.compile(r'(?:加入你将获得|你将获得|实习生收益|实习收获)[^\n]*(?:\n[^\n]*){0,3}?(?:一作|第一作者|署名)[^\n]*')
EXPLICIT_MENTOR = re.compile(r'(?:合作导师|指导老师|指导教师|课题组负责人|导师)\s*[：:]\s*[^\n]{1,80}')
MENTOR = re.compile(r'(?:导师|老师|教授|课题组|实验室)[^。\n]{0,30}(?:招募|招收|招聘|招新)|(?:合作导师|指导老师)[：:][^\n]{1,80}')
TIME_COMMITMENT = re.compile(r'立即开始|未来两周|两周内|每周[^。\n]{0,12}(?:天|小时)|至少[^。\n]{0,8}个月')
DEADLINE = re.compile(r'(?:截止|截至)\s*[^。\n]{0,16}?(?:(20\d{2})\s*[-/年])?\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*(?:日|号)?')


def quote(pattern, text):
    match = pattern.search(text)
    if not match:
        return ''
    start = max(text.rfind('\n', 0, match.start()) + 1, match.start() - 35)
    end = text.find('\n', match.end())
    return text[start:min(end if end >= 0 else len(text), match.end() + 100)].strip()


def opportunity(item, current=None):
    current = current or date.today()
    title = item.get('title', '')
    body = item.get('body', '').split('精彩回复', 1)[0]
    text = title + '\n' + body
    subject = title + '\n' + body[:1200]
    content = body.split('\n\n', 1)[1] if body.startswith('发信人:') and '\n\n' in body else body
    recruitment = quote(RECRUIT, content) or quote(RECRUIT, title)
    candidate = bool(RECRUIT.search(subject) and (RESEARCH.search(title) or
                     (RECRUIT.search(title) and CONTEXT.search(body[:1200])) or
                     (DIRECT_RECRUIT.search(content[:1200]) and RESEARCH.search(content[:1200]))))
    undergraduate = quote(UNDERGRADUATE, text)
    graduate = quote(GRADUATE, text)
    closed = quote(CLOSED, text)
    deadline = ''
    deadline_evidence = ''
    deadline_inferred = False
    deadlines = []
    for match in DEADLINE.finditer(body):
        try:
            year = int(match[1] or item.get('published', '')[:4])
            parsed = date(year, int(match[2]), int(match[3]))
            deadlines.append((parsed, match.group(), not bool(match[1])))
        except (ValueError, TypeError):
            continue
    if len({parsed for parsed, evidence, inferred in deadlines}) == 1:
        parsed, deadline_evidence, deadline_inferred = deadlines[0]
        deadline = parsed.isoformat()
        if parsed < current:
            closed = deadline_evidence
    excluded = quote(SEEKER, title) or closed or quote(NO_UNDERGRADUATE, text) or (graduate if not undergraduate else '')
    ongoing = quote(ONGOING, text) if candidate else ''
    authorship = (quote(AUTHORSHIP, text) or quote(BENEFIT_AUTHORSHIP, content)) if candidate else ''
    mentor = (quote(EXPLICIT_MENTOR, text) or quote(MENTOR, text)) if candidate else ''
    return {
        'candidate': candidate and not bool(excluded),
        'recruitment': recruitment,
        'undergraduate': undergraduate,
        'eligibility': 'explicit' if undergraduate else ('graduate_only' if graduate else 'unknown'),
        'ongoing': ongoing,
        'authorship': authorship,
        'mentor': mentor,
        'time_commitment': quote(TIME_COMMITMENT, content) if candidate else '',
        'deadline': deadline,
        'deadline_evidence': deadline_evidence,
        'deadline_inferred': deadline_inferred,
        'exclusion': excluded,
        'priority': (1 + bool(undergraduate) + bool(ongoing) + bool(authorship)) if candidate and not excluded else 0,
    }
