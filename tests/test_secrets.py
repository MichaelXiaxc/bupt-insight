import json
import os

import pytest

from core import secrets
from core.errors import InsightError


def test_credentials_are_encrypted_at_rest(cfg):
    secrets.save_byr_credentials(cfg, "20230001", "s3cret-password")
    store = secrets.byr_credentials_store(cfg)
    raw = store.path.read_text(encoding="utf-8")
    assert "s3cret-password" not in raw
    assert "20230001" not in raw
    envelope = json.loads(raw)
    assert envelope["alg"] == "AES-256-GCM" and envelope["v"] == 1
    value = secrets.load_byr_credentials(cfg)
    assert value["username"] == "20230001"
    assert value["password"] == "s3cret-password"


def test_missing_credentials_is_a_typed_error(cfg):
    with pytest.raises(InsightError) as excinfo:
        secrets.load_byr_credentials(cfg)
    assert excinfo.value.status == "secrets_missing"
    assert secrets.credentials_status(cfg)["configured"] is False


def test_wrong_key_cannot_decrypt(cfg):
    secrets.save_byr_credentials(cfg, "u", "p")
    key = secrets.master_key_path(cfg)
    key.unlink()
    # A different key is generated on demand and must not open the old ciphertext.
    assert secrets.load_master_key(key, create=True)
    with pytest.raises(InsightError) as excinfo:
        secrets.load_byr_credentials(cfg)
    assert excinfo.value.status == "secrets_corrupt"


def test_ciphertext_is_bound_to_its_purpose(cfg):
    secrets.save_byr_credentials(cfg, "u", "p")
    credential_file = secrets.byr_credentials_store(cfg).path
    state_file = secrets.byr_state_store(cfg).path
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_bytes(credential_file.read_bytes())
    with pytest.raises(InsightError):
        secrets.byr_state_store(cfg).read()


def test_environment_master_key_never_touches_disk(cfg, monkeypatch):
    key = os.urandom(32)
    monkeypatch.setenv(secrets.ENV_MASTER_KEY, key.hex())
    secrets.save_byr_credentials(cfg, "u2", "p2")
    assert not secrets.master_key_path(cfg).exists()
    assert secrets.load_byr_credentials(cfg)["password"] == "p2"
    status = secrets.credentials_status(cfg)
    assert status["key_source"] == "environment"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission check")
def test_world_readable_secret_is_refused(cfg):
    secrets.save_byr_credentials(cfg, "u", "p")
    os.chmod(secrets.byr_credentials_store(cfg).path, 0o644)
    with pytest.raises(InsightError) as excinfo:
        secrets.load_byr_credentials(cfg)
    assert excinfo.value.status == "secrets_permissions"


def test_session_secret_is_stable(cfg):
    first = secrets.session_secret(cfg)
    second = secrets.session_secret(cfg)
    assert first == second and len(first) > 20


def test_clear_removes_only_credentials(cfg):
    secrets.save_byr_credentials(cfg, "u", "p")
    secrets.byr_state_store(cfg).write({"cookies": [], "origins": []})
    secrets.clear_byr_credentials(cfg)
    assert not secrets.byr_credentials_store(cfg).exists()
    assert secrets.byr_state_store(cfg).exists()
