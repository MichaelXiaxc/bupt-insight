import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def pytest_configure(config):
    # Avoid inheriting inaccessible temp folders created by other Windows identities.
    (pathlib.Path(config.rootpath) / ".test-output").mkdir(exist_ok=True)


@pytest.fixture
def cfg(tmp_path):
    return {
        "owner": {"tz": "Asia/Shanghai"},
        "site": {"title": "BUPT Insight Test", "base_url": "", "allowed_hosts": [], "cookie_secure": False},
        "server": {"host": "127.0.0.1", "port": 8765},
        "net": {"browser": "chrome", "executable_path": "", "timeout_ms": 1000, "retries": 1, "delay_ms": 0, "args": []},
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "session_dir": str(tmp_path / "sessions"),
            "inbox_dir": str(tmp_path / "inbox"),
            "secrets_dir": str(tmp_path / "secrets"),
        },
        "sources": {
            "byr": {
                "enabled": True,
                "base": "https://bbs.byr.cn",
                "requires_login": True,
                "private": False,
                "watchlist": "watchlist.json",
                "boards": ["Paper"],
                "rows_per_board": 2,
                "board_overrides": {},
                "credentials_file": "",
                "session_file": "",
                "master_key_file": "",
            }
        },
        "push": {"retries": 1, "channels": {}},
        "backup": {"enabled": True, "interval_hours": 24, "keep": 7},
        "health": {"stale_hours": 24},
        "ai": {"mode": "off"},
        "smart": {"enabled": False},
    }


@pytest.fixture
def notice():
    from core.schema import Item

    return Item(
        source="byr",
        channel="科研与论文",
        title="虚构实验室招募本科科研助理",
        url="https://bbs.byr.cn/article/Paper/12345",
        published="2026-10-20 10:00:00",
        author="fictional",
        body="发信人: fictional\n\n虚构实验室招募科研助理，要求掌握 Python，报名截止 2026-11-20。",
        extra={"body_status": "ok"},
    )
