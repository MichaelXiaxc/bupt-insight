"""Configuration loading, path resolution and deployment defaults.

Configuration precedence (later wins):

1. built-in defaults below;
2. ``config.yaml`` next to the project (or ``$BUPT_INSIGHT_CONFIG``);
3. a small set of environment variables for orchestrators (see README).

Secrets never live in this file: it only points at the encrypted stores.
"""

from __future__ import annotations

import copy
import os
import pathlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from core.errors import InsightError

ROOT = pathlib.Path(__file__).resolve().parent.parent

BOARDS = ["Paper", "ML_DM", "Robot", "SICE", "ParttimeJob", "MathModel", "Innovation", "ACM_ICPC", "AcademicAffairs", "SCS"]

DEFAULTS: dict = {
    "owner": {"tz": "Asia/Shanghai"},
    "site": {
        "title": "BUPT Insight",
        "base_url": "",
        "allowed_hosts": [],
        "cookie_secure": None,  # None = derive from base_url scheme
    },
    "server": {"host": "127.0.0.1", "port": 8000},
    "net": {
        "browser": "chrome",
        "executable_path": "",
        "timeout_ms": 30000,
        "retries": 2,
        "delay_ms": 2500,
        "args": [],
    },
    "paths": {
        "data_dir": ".local/data",
        "session_dir": ".local/sessions",
        "inbox_dir": ".local/inbox",
        "secrets_dir": ".local/secrets",
    },
    "sources": {
        "byr": {
            "enabled": True,
            "base": "https://bbs.byr.cn",
            "requires_login": True,
            "private": False,
            "watchlist": "watchlist.json",
            "boards": BOARDS,
            "rows_per_board": 10,
            "board_overrides": {"Paper": {"pages": 3, "rows_per_page": 30}},
            "credentials_file": "",
            "session_file": "",
            "master_key_file": "",
        }
    },
    "push": {"retries": 3, "channels": {}},
    "backup": {"enabled": True, "interval_hours": 24, "keep": 7},
    "health": {"stale_hours": 24},
    "ai": {
        "mode": "off",
        "base_url": "",
        "model": "",
        "response_format": "json_schema",
        "timeout_seconds": 8,
        "retries": 1,
        "budget_seconds": 12,
        "max_per_run": 20,
        "max_input_chars": 6000,
        "allow_private_cloud": False,
        "allow_profile_cloud": False,
    },
    "smart": {"enabled": False},
}


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def config_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("BUPT_INSIGHT_CONFIG") or (ROOT / "config.yaml"))


def load_config(path=None) -> dict:
    source = pathlib.Path(path) if path else config_path()
    if not source.exists():
        raise SystemExit(
            f"找不到配置文件 {source}。先运行：\n    python -X utf8 run.py init\n"
            "然后编辑 config.yaml，并运行 run.py credentials save 保存 BYR 账号。"
        )
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        raise SystemExit(f"配置文件 {source} 不是合法 YAML") from None
    cfg = _merge(DEFAULTS, value)
    if not isinstance(cfg.get("sources"), dict) or "byr" not in cfg["sources"]:
        raise SystemExit("配置缺少 sources.byr")
    _apply_env(cfg)
    return cfg


def _apply_env(cfg: dict) -> None:
    if os.environ.get("BUPT_INSIGHT_DATA_DIR"):
        cfg["paths"]["data_dir"] = os.environ["BUPT_INSIGHT_DATA_DIR"]
    if os.environ.get("BUPT_INSIGHT_SECRETS_DIR"):
        cfg["paths"]["secrets_dir"] = os.environ["BUPT_INSIGHT_SECRETS_DIR"]


def resolve(cfg, relpath: str) -> pathlib.Path:
    path = pathlib.Path(os.path.expandvars(str(relpath)))
    return path if path.is_absolute() else ROOT / path


def data_dir(cfg) -> pathlib.Path:
    return resolve(cfg, cfg["paths"]["data_dir"])


def secrets_dir(cfg) -> pathlib.Path:
    return resolve(cfg, cfg["paths"].get("secrets_dir", ".local/secrets"))


def ensure_dirs(cfg) -> None:
    for value in (data_dir(cfg), resolve(cfg, cfg["paths"]["session_dir"]), resolve(cfg, cfg["paths"]["inbox_dir"]), secrets_dir(cfg)):
        value.mkdir(parents=True, exist_ok=True)
        if os.name == "posix" and value.name in ("secrets", ".local"):
            os.chmod(value, 0o700)


def timezone(cfg) -> ZoneInfo:
    name = cfg.get("owner", {}).get("tz") or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise InsightError("configuration_error", f"未知时区 {name}；请安装 tzdata 或修改 owner.tz") from None


def allowed_hosts(cfg, port: int | None = None) -> set[str]:
    port = int(port or cfg.get("server", {}).get("port", 8000))
    values = {str(item).strip().lower() for item in (cfg.get("site", {}).get("allowed_hosts") or []) if str(item).strip()}
    values.update(item.strip().lower() for item in os.environ.get("INSIGHT_ALLOWED_HOSTS", "").split(",") if item.strip())
    for host in ("127.0.0.1", "localhost"):
        values.add(host)
        values.add(f"{host}:{port}")
    return values


def cookie_secure(cfg) -> bool:
    explicit = cfg.get("site", {}).get("cookie_secure")
    if explicit is not None:
        return bool(explicit)
    return str(cfg.get("site", {}).get("base_url", "")).lower().startswith("https://")
