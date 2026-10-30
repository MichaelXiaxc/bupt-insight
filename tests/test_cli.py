import json
import sys

import yaml

from core import cli


class TTY:
    def isatty(self):
        return True


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
    return path, value


def test_version_command(capsys):
    assert cli.main(["version"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"]


def test_credentials_save_stores_encrypted_and_reports_status(tmp_path, capsys, monkeypatch):
    path, _ = write_config(tmp_path)
    monkeypatch.setenv("BUPT_INSIGHT_CONFIG", str(path))
    monkeypatch.setattr(sys, "stdin", TTY())
    values = iter(["20230001", "top-secret"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(values))

    assert cli.main(["credentials", "save"]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["configured"] is True
    assert saved["username"] == "20230001"
    assert "top-secret" not in json.dumps(saved)

    encrypted = (tmp_path / "secrets" / "byr.enc").read_text(encoding="utf-8")
    assert "top-secret" not in encrypted

    assert cli.main(["credentials", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is True


def test_credentials_clear(tmp_path, capsys, monkeypatch):
    path, _ = write_config(tmp_path)
    monkeypatch.setenv("BUPT_INSIGHT_CONFIG", str(path))
    monkeypatch.setattr(sys, "stdin", TTY())
    values = iter(["u", "p"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(values))
    cli.main(["credentials", "save"])
    capsys.readouterr()
    assert cli.main(["credentials", "clear"]) == 0
    assert not (tmp_path / "secrets" / "byr.enc").exists()


def test_scan_rejects_unknown_source(tmp_path, capsys, monkeypatch):
    path, _ = write_config(tmp_path)
    monkeypatch.setenv("BUPT_INSIGHT_CONFIG", str(path))
    assert cli.main(["scan", "portal"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "configuration_error"
