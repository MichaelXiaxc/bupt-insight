"""Playwright session management for BYR.

The login snapshot is a credential: it is stored through ``core.secrets`` as
AES-256-GCM ciphertext, never as a plain storage_state file. The login flow
prefills the saved account when it can find the form, then waits for a real
board page to load before accepting the session.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import platform
import shutil
import sys
import time

from core.config import resolve
from core.errors import InsightError
from core.secrets import byr_state_store, load_byr_credentials

log = logging.getLogger(__name__)

BROWSER_ARGS: list[str] = []  # Do not hide automation markers or bypass school policy.

_WIN_EDGE = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_WIN_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_MAC_EDGE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_LINUX = ["/usr/bin/google-chrome", "/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"]


def _known_paths(which: str):
    """Common install locations per platform, then PATH."""
    sysname = platform.system()
    if which in ("edge", "msedge"):
        fixed = _WIN_EDGE if sysname == "Windows" else ([_MAC_EDGE] if sysname == "Darwin" else [])
        return fixed + [shutil.which("microsoft-edge"), shutil.which("microsoft-edge-stable")]
    if which == "chrome":
        fixed = _WIN_CHROME if sysname == "Windows" else ([_MAC_CHROME] if sysname == "Darwin" else _LINUX)
        return fixed + [shutil.which("google-chrome"), shutil.which("google-chrome-stable")]
    if which == "chromium":
        return _LINUX + [shutil.which("chromium"), shutil.which("chromium-browser")]
    return []


def _pw_cache():
    if platform.system() == "Windows":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local"))
        return base / "ms-playwright"
    if platform.system() == "Darwin":
        return pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
    return pathlib.Path.home() / ".cache" / "ms-playwright"


# Only the full chromium build; the headless shell is a separate download and a
# common source of "Executable doesn't exist" on servers.
_PW_CHROMIUM = [
    "chromium-*/chrome-win64/chrome.exe",
    "chromium-*/chrome-win/chrome.exe",
    "chromium-*/chrome-linux64/chrome",
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
]


def playwright_chromium():
    base = _pw_cache()
    hits = []
    for pattern in _PW_CHROMIUM:
        hits += list(base.glob(pattern))
    return str(sorted(hits)[-1]) if hits else None


def browser_exe(cfg=None):
    net = ((cfg or {}).get("net") or {})
    if net.get("executable_path"):
        return net["executable_path"]
    which = str(net.get("browser", "chrome")).lower()
    for path in _known_paths(which):
        if path and pathlib.Path(path).exists():
            return path
    return playwright_chromium()


_CHANNEL = {"edge": "msedge", "chrome": "chrome"}


def launch(pw, cfg=None, headless=True):
    net = ((cfg or {}).get("net") or {})
    args = list(net.get("args") or [])
    which = str(net.get("browser", "chrome")).lower()
    if not net.get("executable_path") and which in _CHANNEL:
        try:
            return pw.chromium.launch(headless=headless, channel=_CHANNEL[which], args=args)
        except Exception:
            pass  # Fall back to the bundled build rather than breaking the pipeline.
    executable = browser_exe(cfg)
    if executable:
        return pw.chromium.launch(headless=headless, executable_path=executable, args=args)
    return pw.chromium.launch(headless=headless, args=args)


def harden(ctx):
    """Keep the original interface; browser fingerprinting is not modified."""
    return ctx


class Session:
    """Wraps the Playwright lifecycle; business code only asks for a page."""

    def __init__(self, cfg, source_key: str = "byr"):
        self.cfg = cfg
        self.key = source_key
        self.store = byr_state_store(cfg)
        self.base = cfg["sources"][source_key].get("base")
        self.requires_login = cfg["sources"][source_key].get("requires_login", True)
        self.timeout = cfg["net"]["timeout_ms"]
        self._pw = None
        self._browser = None
        self.ctx = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        has_state = self.store.exists()
        self.logged_in = has_state
        if not has_state and self.requires_login:
            raise InsightError("login_expired", "没有会话快照。先运行：python -X utf8 run.py login byr")
        try:
            self._pw = sync_playwright().start()
            self._browser = launch(self._pw, self.cfg, headless=True)
            state = self.store.read() if has_state else None
            self.ctx = harden(self._browser.new_context(storage_state=state))
            self.ctx.set_default_timeout(self.timeout)
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        if self.ctx:
            self.ctx.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def new_page(self):
        return self.ctx.new_page()

    def save(self):
        self.store.write(self.ctx.storage_state())

    def cookie(self, *names):
        want = {n.lower() for n in names}
        for cookie in self.ctx.cookies():
            if cookie["name"].lower() in want and cookie["value"] not in ("", "guest"):
                return cookie["value"]
        return None


def byr_probe(page):
    """Login marker: a real nforum user cookie, not just any random cookie."""
    for cookie in page.context.cookies():
        if cookie["name"] in ("nforum[UTMPUSERID]", "login-user") and cookie["value"] not in ("", "guest"):
            return "logged-in-marker"
    return None


def byr_verify(page, board="Paper"):
    page.goto(f"https://bbs.byr.cn/board/{board}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("table.board-list tr", timeout=20000)
    return page.locator("table.board-list tr").count() > 0


def _fill_login(page, username, password) -> bool:
    """Best-effort prefill of the official form; never log the values."""
    try:
        user = page.locator('input[name="id"], #id, input[name="username"]').first
        pwd = page.locator('input[name="passwd"], input[name="pwd"], #pwd, input[name="password"]').first
        if not (user.count() and pwd.count() and user.is_visible()):
            return False
        user.fill(username)
        pwd.fill(password)
        # Scope the submit button to the same form: the page header also has a
        # "登录" control that would otherwise be matched first.
        form = pwd.locator("xpath=ancestor::form[1]")
        submit = form.locator('input[type="submit"], button[type="submit"], button:has-text("登录")').first
        if submit.count():
            submit.click()
        else:
            pwd.press("Enter")
        return True
    except Exception:
        return False
    return False


def login(cfg, start_url=None, wait_seconds: int = 300) -> bool:
    """Open a real browser, authenticate (prefilled when possible), save a session."""
    from playwright.sync_api import sync_playwright

    source = cfg["sources"]["byr"]
    start_url = start_url or source.get("base") or "https://bbs.byr.cn/index"
    store = byr_state_store(cfg)
    try:
        credentials = load_byr_credentials(cfg)
    except InsightError:
        credentials = None

    with sync_playwright() as pw:
        browser = launch(pw, cfg, headless=False)
        ctx = harden(browser.new_context())
        page = ctx.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)

        if credentials and _fill_login(page, credentials["username"], credentials["password"]):
            print("已用加密保存的账号提交登录表单。若出现验证码/二次验证，请在浏览器中完成。")
        else:
            print("请在浏览器窗口中完成 BYR 登录。")

        print(f"最多等待 {wait_seconds} 秒；登录标记与凭据不会输出到日志。")
        before = byr_probe(page)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(3)
            try:
                current = byr_probe(page)
            except Exception:
                continue
            if current and current != before or (current and not before):
                try:
                    verified = byr_verify(page)
                except Exception:
                    verified = False
                if not verified:
                    print("  检测到登录标记，但版面自检未通过，继续等待…")
                    continue
                store.write(ctx.storage_state())
                print("登录已通过版面验证，会话已加密保存。")
                ctx.close()
                browser.close()
                return True

        print("等待超时，未保存会话。重新运行 login byr 即可。")
        ctx.close()
        browser.close()
        return False


def import_state(cfg, file) -> None:
    """Import a plain Playwright storage_state JSON and encrypt it."""
    path = pathlib.Path(file)
    if not path.exists():
        raise InsightError("configuration_error", f"找不到 {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InsightError("configuration_error", "会话文件不是合法的 storage_state JSON") from None
    if not isinstance(value, dict) or "cookies" not in value:
        raise InsightError("configuration_error", "会话文件缺少 cookies 字段")
    byr_state_store(cfg).write(value)
    print(f"已加密导入 {path.name}；建议删除原始明文文件：{path}")


def token_probe():
    """Generic login detector kept for sources that have no stable DOM marker."""
    js = """() => {
      const parts = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i), v = localStorage.getItem(k) || '';
          if (/token|eyJ|userId|uid|userInfo|studentId|user_id|phone|realName/i.test(k + v))
            parts.push('LS:' + k + '=' + v.slice(0, 80));
        }
      } catch (e) {}
      const cks = (document.cookie || '').split(';').map(s => s.trim()).filter(Boolean);
      for (const c of cks) {
        if (/token|eyJ|uid|user|sid|SESSION|login|auth/i.test(c)) parts.push('CK:' + c.slice(0, 80));
      }
      return parts.length ? parts.sort().join('|') : null;
    }"""
    return lambda p: p.evaluate(js)
