"""Uniform item structure. Every source produces Items; downstream code only sees Items."""

from __future__ import annotations

import dataclasses
import datetime
import re
import typing


@dataclasses.dataclass
class Item:
    source: str  # byr
    channel: str  # board name
    title: str
    url: str

    # Three different meanings; do not conflate them.
    published: str = ""  # original post date
    last_active: str = ""  # last reply (or notice update)
    deadline: str = ""  # sign-up deadline, highest priority when present

    # Event-like entries (talks/activities) need time and place to be actionable.
    start_time: str = ""
    place: str = ""
    capacity: str = ""

    author: str = ""
    replies: int = 0
    score: int = 0
    reasons: typing.List[str] = dataclasses.field(default_factory=list)
    extra: dict = dataclasses.field(default_factory=dict)
    body: str = ""
    private: bool = False

    def __post_init__(self):
        # Web innerText often carries newlines and a whole paragraph; collapse to one line.
        self.title = re.sub(r"\s+", " ", (self.title or "")).strip()

    def key(self) -> str:
        """Deduplication key: URL without tracking/credential query parameters."""
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        p = urlsplit(self.url)
        query = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in ("ticket", "jsessionid") and not k.lower().startswith("utm_")
        ]
        return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), urlencode(sorted(query)), p.fragment))

    _DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

    def parse_day(self, field="published"):
        s = (getattr(self, field) or "").strip()
        # Match the whole string first; never truncate it to the format length.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        match = self._DATE_RE.search(s)
        if match:
            try:
                return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
        return None

    def age_days(self, today=None) -> typing.Optional[int]:
        """Days since the newer of published/last_active."""
        today = today or datetime.date.today()
        days = [d for d in (self.parse_day("published"), self.parse_day("last_active")) if d]
        if not days:
            return None
        return (today - max(days)).days

    def urgency(self) -> typing.Optional[int]:
        d = self.parse_day("deadline")
        return (d - datetime.date.today()).days if d else None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
