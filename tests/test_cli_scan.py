import json

import yaml

from core import cli
from core.config import data_dir, load_config
from core.locking import single_run


def write_config(tmp_path):
    value = {
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "session_dir": str(tmp_path / "sessions"),
            "inbox_dir": str(tmp_path / "inbox"),
            "secrets_dir": str(tmp_path / "secrets"),
        },
        "sources": {"byr": {"enabled": True, "boards": ["Paper"]}},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return path


def test_scan_does_not_double_lock(tmp_path, monkeypatch, capsys):
    """`sync` owns the writer lock; the CLI must not hold it as well."""
    path = write_config(tmp_path)
    monkeypatch.setenv("BUPT_INSIGHT_CONFIG", str(path))
    entered = []

    def fake_sync(cfg, *args, **kwargs):
        with single_run(data_dir(cfg) / "run.lock"):
            entered.append(True)
        return {
            "sources": {"byr": {"status": "no_new", "new": 0, "changed": 0, "unchanged": 0, "errors": []}},
            "deliveries": [],
        }

    monkeypatch.setattr(cli, "sync", fake_sync)
    assert cli.main(["scan"]) == 0
    assert entered == [True]
    assert json.loads(capsys.readouterr().out)["sources"]["byr"]["status"] == "no_new"
