"""Encrypted, file-based secret storage.

Only two artifacts are sensitive in this project: the BYR account credentials
and the Playwright session snapshot (cookies). Both are stored as AES-256-GCM
ciphertext in files whose key lives in a **separate** key file, or is supplied
through ``BUPT_INSIGHT_MASTER_KEY`` for orchestrators that inject secrets.

Isolation rules enforced here:

* the secrets directory is created ``0700`` and files ``0600`` (POSIX); a file
  with a wider mode is refused rather than silently used;
* the key never sits next to the ciphertext by default (``master.key`` can be
  provided from outside the data directory or the environment);
* every ciphertext is bound to its purpose through AES-GCM associated data, so
  swapping the credentials file and the session file fails to decrypt;
* nothing is ever echoed to logs or error messages.

This protects backups, stray copies and casual reads on a shared host. It does
**not** protect against an attacker who can read the key file or execute as the
service user; disk encryption and OS access control remain the outer layers.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.config import resolve
from core.errors import InsightError

VERSION = 1
ALGORITHM = "AES-256-GCM"
KEY_BYTES = 32
NONCE_BYTES = 12
ENV_MASTER_KEY = "BUPT_INSIGHT_MASTER_KEY"
ENV_SESSION_KEY = "INSIGHT_SECRET_KEY"


def _b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_key(text: str) -> bytes:
    text = (text or "").strip()
    if len(text) == 2 * KEY_BYTES:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise InsightError("secrets_invalid_key", "主密钥格式无效；应为 32 字节的 base64 或 64 位十六进制") from None
    if len(raw) != KEY_BYTES:
        raise InsightError("secrets_invalid_key", "主密钥长度无效；应为 32 字节")
    return raw


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_private(path: Path, *, directory: bool = False) -> None:
    """Refuse group/world accessible secrets on POSIX. Windows uses ACLs."""
    if os.name != "posix" or not path.exists():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        wanted = "0700" if directory else "0600"
        raise InsightError(
            "secrets_permissions",
            f"{path.name} 权限过宽（{oct(mode)}），应为 {wanted}；请执行 chmod {wanted} 后重试",
        )


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_master_key(key_path: Path, *, create: bool = False) -> bytes:
    """Return the 32-byte master key, optionally creating the key file."""
    supplied = os.environ.get(ENV_MASTER_KEY)
    if supplied:
        return _decode_key(supplied)
    path = Path(key_path)
    if path.exists():
        _require_private(path)
        try:
            return _decode_key(path.read_text(encoding="utf-8"))
        except OSError:
            raise InsightError("secrets_unreadable", "主密钥文件无法读取") from None
    if not create:
        raise InsightError(
            "secrets_missing_key",
            f"缺少主密钥 {path.name}；先运行 run.py credentials save，或设置 {ENV_MASTER_KEY}",
        )
    key = secrets.token_bytes(KEY_BYTES)
    _write_private(path, _b64e(key).encode("ascii"))
    return key


def derive(key: bytes, purpose: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=b"bupt-insight/" + purpose).derive(key)


class EncryptedStore:
    """One ciphertext file bound to one purpose."""

    def __init__(self, path: Path, key_path: Path, purpose: str):
        self.path = Path(path)
        self.key_path = Path(key_path)
        self.purpose = purpose.encode("utf-8")
        self._key: bytes | None = None

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = load_master_key(self.key_path)
        return self._key

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict:
        if not self.exists():
            raise InsightError("secrets_missing", f"缺少 {self.path.name}；请先运行 run.py credentials save")
        _require_private(self.path)
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if envelope.get("v") != VERSION or envelope.get("alg") != ALGORITHM:
                raise ValueError()
            plaintext = AESGCM(self.key).decrypt(
                base64.b64decode(envelope["nonce"], validate=True),
                base64.b64decode(envelope["ciphertext"], validate=True),
                self.purpose,
            )
            return json.loads(plaintext.decode("utf-8"))
        except InsightError:
            raise
        except Exception:
            # Never surface ciphertext, key material or provider messages.
            raise InsightError("secrets_corrupt", f"{self.path.name} 无法解密；文件损坏、密钥不符或已被替换") from None

    def write(self, payload: dict) -> None:
        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        envelope = {
            "v": VERSION,
            "alg": ALGORITHM,
            "purpose": self.purpose.decode("utf-8"),
            "created_at": _now(),
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(AESGCM(self.key).encrypt(nonce, plaintext, self.purpose)),
        }
        _write_private(self.path, json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8"))


def secrets_dir(cfg) -> Path:
    return resolve(cfg, cfg["paths"].get("secrets_dir") or ".local/secrets")


def _byr_path(cfg, key: str, filename: str) -> Path:
    configured = cfg["sources"]["byr"].get(key) or ""
    if configured:
        return resolve(cfg, configured)
    return secrets_dir(cfg) / filename


def master_key_path(cfg) -> Path:
    return _byr_path(cfg, "master_key_file", "master.key")


def byr_credentials_store(cfg) -> EncryptedStore:
    return EncryptedStore(_byr_path(cfg, "credentials_file", "byr.enc"), master_key_path(cfg), "byr-credentials")


def byr_state_store(cfg) -> EncryptedStore:
    return EncryptedStore(_byr_path(cfg, "session_file", "byr-state.enc"), master_key_path(cfg), "byr-session")


def save_byr_credentials(cfg, username: str, password: str) -> None:
    username, password = username.strip(), password
    if not username or not password:
        raise InsightError("credentials_missing", "账号和密码不能为空")
    store = byr_credentials_store(cfg)
    load_master_key(store.key_path, create=True)  # create the key before the ciphertext
    store.write({"username": username, "password": password, "saved_at": _now()})


def load_byr_credentials(cfg) -> dict:
    value = byr_credentials_store(cfg).read()
    if not value.get("username") or not value.get("password"):
        raise InsightError("credentials_missing", "加密凭据缺少账号或密码；请重新运行 credentials save")
    return value


def credentials_status(cfg) -> dict:
    store = byr_credentials_store(cfg)
    result = {
        "configured": False,
        "file": str(store.path),
        "key_file": str(store.key_path),
        "key_source": "environment" if os.environ.get(ENV_MASTER_KEY) else ("file" if store.key_path.exists() else "missing"),
    }
    if not store.exists():
        return result
    try:
        value = store.read()
        result.update(configured=True, username=value.get("username", ""), saved_at=value.get("saved_at", ""))
    except InsightError as exc:
        result["error"] = exc.status
    return result


def clear_byr_credentials(cfg) -> None:
    byr_credentials_store(cfg).path.unlink(missing_ok=True)


def clear_byr_state(cfg) -> None:
    byr_state_store(cfg).path.unlink(missing_ok=True)


def llm_key_store(cfg) -> EncryptedStore:
    return EncryptedStore(secrets_dir(cfg) / "llm.enc", master_key_path(cfg), "llm-key")


def save_llm_key(cfg, value: str) -> None:
    if not value.strip():
        raise InsightError("credentials_missing", "AI 密钥不能为空")
    store = llm_key_store(cfg)
    load_master_key(store.key_path, create=True)
    store.write({"key": value.strip(), "saved_at": _now()})


def load_llm_key(cfg) -> str | None:
    store = llm_key_store(cfg)
    if not store.exists():
        return None
    try:
        return store.read().get("key")
    except InsightError:
        return None


def clear_llm_key(cfg) -> None:
    llm_key_store(cfg).path.unlink(missing_ok=True)


def session_secret(cfg) -> str:
    """Stable Flask signing key so sessions survive restarts and workers."""
    supplied = os.environ.get(ENV_SESSION_KEY)
    if supplied:
        return supplied
    path = secrets_dir(cfg) / "session.key"
    if path.exists():
        _require_private(path)
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    try:
        _write_private(path, value.encode("utf-8"))
    except OSError:
        print("警告：无法写入会话密钥文件，将使用进程内随机密钥（重启后会话失效）", file=sys.stderr)
    return value
