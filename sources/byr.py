"""北邮人论坛（BYR）适配器。

版面是 hash 路由的 SPA，`networkidle` 不稳；用 domcontentloaded + 等选择器 +
重试。置顶行常年不动，必须跳过，否则每次都会被当成新帖。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta

from core.browser import Session
from core.config import resolve
from core.errors import InsightError, safe_error
from core.progress import update as progress
from core.schema import Item

JS_ROWS = """() => {
    const t = document.querySelector('table.board-list');
    if (!t) return [];
    const out = [];
    for (const tr of t.querySelectorAll('tr')) {
        if (tr.classList.contains('top')) continue;
        const tds = Array.from(tr.querySelectorAll('td'));
        if (tds.length < 5) continue;
        const a = tds[1].querySelector("a[href*='/article/']");
        if (!a) continue;
        const cells = tds.map(c => c.innerText.replace(/\\s+/g, ' ').trim());
        out.push({
            title: (a.innerText || '').trim(),
            url: new URL(a.getAttribute('href').replace(/^#!?/, ''), 'https://bbs.byr.cn').href,
            author: (cells[3] || '').replace(/^\\|\\s*/, ''),
            published: cells[2] || '',
            replies: parseInt(cells[4] || '0', 10) || 0,
            last_active: cells[5] || ''
        });
    }
    return out;
}"""

# nforum 正文在 class 带文章号的 td.a-content 里。不要用 document.body 兜底，
# 否则抓回来的是满屏导航栏。
JS_BODY = """() => {
    const el = document.querySelector('td.a-content');
    return el ? el.innerText : '';
}"""


def read(cfg, url: str, max_chars: int = 1200) -> tuple[str, str]:
    """Read one post."""
    import re

    with Session(cfg, "byr") as session:
        page = session.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        check_login(page, session)
        page.wait_for_selector("td.a-content", timeout=40000)
        body = page.evaluate(JS_BODY) or ""
        return page.title(), re.sub(r"\n{3,}", "\n\n", body.strip())[:max_chars]


def load_watchlist(cfg, only=None):
    wl = json.loads(resolve(cfg, cfg["sources"]["byr"].get("watchlist", "watchlist.json")).read_text(encoding="utf-8"))
    wl = {k: v for k, v in wl.items() if not k.startswith("_")}
    if only:
        want = {b.strip() for b in only.split(",")}
        wl = {k: v for k, v in wl.items() if k in want}
    configured = cfg["sources"]["byr"].get("boards")
    if configured and not only:
        wl = {k: v for k, v in wl.items() if k in configured}
    if not wl:
        raise InsightError("configuration_error", "BYR 关注版面为空或版面代号不在 watchlist.json")
    return wl


def check_login(page, session):
    if not session.cookie("nforum[UTMPUSERID]", "login-user"):
        raise InsightError("login_expired", "BYR 会话失效，请重新运行 login byr")
    form = page.locator('form[action="/login"] input[name="passwd"]')
    if form.count() and form.first.is_visible():
        raise InsightError("login_expired", "BYR 返回访客登录表单，请重新登录")


def fetch(cfg, only=None, quiet=False):
    """Fetch every watched board; returns {board: [Item]}. No filtering or scoring."""
    watch = load_watchlist(cfg, only)
    source = cfg["sources"]["byr"]
    rows_per = source["rows_per_board"]
    overrides = source.get("board_overrides", {})
    timeout = cfg["net"]["timeout_ms"] + 10000
    tries = cfg["net"]["retries"]
    delay = cfg["net"]["delay_ms"] / 1000

    fetched: dict[str, list] = {}
    errors: list[str] = []

    with Session(cfg, "byr") as session:
        if not session.cookie("nforum[UTMPUSERID]", "login-user"):
            raise InsightError("login_expired", "BYR 会话失效，请重跑 python -X utf8 run.py login byr")
        if not quiet:
            print(f"BYR 会话待业务验证，版面 {len(watch)} 个")

        page = session.new_page()

        def grab(board, page_number):
            last = None
            for _ in range(tries):
                try:
                    suffix = f"?p={page_number}" if page_number > 1 else ""
                    page.goto(f"https://bbs.byr.cn/board/{board}{suffix}", wait_until="domcontentloaded", timeout=timeout)
                    check_login(page, session)
                    page.wait_for_selector("table.board-list tr", timeout=20000)
                    page.wait_for_timeout(400)
                    rows = page.evaluate(JS_ROWS)
                    if not rows:
                        raise InsightError("structure_changed", "BYR 列表没有可解析帖子；请检查版面或选择器")
                    return rows
                except Exception as exc:
                    if isinstance(exc, InsightError) and exc.status == "login_expired":
                        raise
                    last = exc
                    page.wait_for_timeout(800)
            raise last

        for board, meta in watch.items():
            progress(cfg, "byr", "board_list", board=board)
            try:
                options = overrides.get(board, {})
                pages = max(1, min(10, int(options.get("pages", 1))))
                limit = max(1, min(100, int(options.get("rows_per_page", rows_per))))
                rows = []
                seen = set()
                for page_number in range(1, pages + 1):
                    time.sleep(delay)
                    try:
                        page_rows = grab(board, page_number)[:limit]
                    except Exception as exc:
                        if page_number == 1:
                            raise
                        status, message = safe_error(exc)
                        errors.append(f"{status}: {board} 第{page_number}页读取失败：{message}")
                        break
                    fresh = [row for row in page_rows if row["url"] not in seen]
                    if not fresh:
                        break
                    for row in fresh:
                        seen.add(row["url"])
                        if len(row["published"]) == 8 and row["published"][2:3] == ":":
                            day = datetime.now(timezone(timedelta(hours=8))).date()
                            row["published"] = f"{day.isoformat()} {row['published']}"
                    rows.extend(fresh)
                fetched[board] = [
                    Item(
                        source="byr",
                        channel=meta["name"],
                        private=cfg["sources"]["byr"].get("private", False),
                        extra={"body_status": "pending"},
                        **row,
                    )
                    for row in rows
                ]
                for item_index, item in enumerate(fetched[board], start=1):
                    progress(cfg, "byr", "post_body", board=board, current=item_index, total=len(fetched[board]))
                    for attempt in range(max(1, tries)):
                        time.sleep(delay)
                        try:
                            page.goto(item.url, wait_until="domcontentloaded", timeout=timeout)
                            check_login(page, session)
                            page.wait_for_selector("td.a-content", timeout=timeout)
                            item.body = (page.evaluate(JS_BODY) or "").strip()
                            if not item.body:
                                raise InsightError("structure_changed", "BYR 正文为空")
                            item.extra["body_status"] = "ok"
                            break
                        except Exception as exc:
                            status, message = safe_error(exc)
                            item.extra["body_status"] = status
                            if status == "login_expired" or attempt == max(1, tries) - 1:
                                errors.append(f"{status}: {board} 正文读取失败：{message}")
                                if status == "login_expired":
                                    return fetched, watch, errors
                                break
                if not quiet:
                    print(f"  {board:18} {meta['name']:16} {len(rows):>3} 条")
            except Exception as exc:
                fetched[board] = []
                status, message = safe_error(exc)
                errors.append(f"{status}: {board}: {message}")
                if not quiet:
                    print(f"  {board:18} {status}")

        # Refresh the stored snapshot only after a run that actually read something.
        if any(fetched.values()):
            session.save()

    return fetched, watch, errors
