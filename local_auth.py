"""Local-only ZEUS login credentials and short-lived browser sessions.

No account credentials are checked into GitHub. Passwords are scrypt-hashed
using a random salt and stored under the configured laptop data directory.
Session tokens are random, stored hashed in RAM, and invalidated on restart.
"""
from collections import deque
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from time import time
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile


COOKIE_NAME = "zeus_session"
SESSION_SECONDS = 8 * 60 * 60
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 10 * 60
MAX_FAILURES = 5
N = 2 ** 15
R = 8
P = 1
MAX_MEM = 64 * 1024 * 1024


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
                          maxmem=MAX_MEM, dklen=32)


@dataclass
class LocalAuth:
    """One local administrator; no network calls, external authentication, or cloud secrets."""

    path: Path

    def __post_init__(self):
        self.path = Path(self.path)
        self.sessions: dict[str, float] = {}
        self.failures = deque(maxlen=50)
        self.blocked_until = 0.0
        self._load()

    def _load(self):
        if not self.path.is_file():
            raise RuntimeError(
                "ZEUS login is not configured. On the HP laptop run: "
                "python bot.py --set-login (in the folder containing bot.py)."
            )
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if record.get("version") != 1 or record.get("algorithm") != "scrypt-n32768-r8-p1":
                raise ValueError("Unsupported local credential format")
            username = record["username"]
            salt = _decode(record["salt"])
            password_hash = _decode(record["password_hash"])
            if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", username):
                raise ValueError("Invalid local username")
            if len(salt) != 16 or len(password_hash) != 32:
                raise ValueError("Invalid local credential material")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Invalid ZEUS local credentials. Reset on the HP laptop using --set-login.") from exc
        self.username = username
        self.salt = salt
        self.password_hash = password_hash

    @staticmethod
    def write_credentials(path: Path, username: str, password: str):
        path = Path(path)
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", username):
            raise ValueError("Username must be 3-32 letters, numbers, underscores or hyphens.")
        if not isinstance(password, str) or not (12 <= len(password) <= 256):
            raise ValueError("Password must be 12-256 characters long.")
        salt = secrets.token_bytes(16)
        record = {
            "version": 1,
            "algorithm": "scrypt-n32768-r8-p1",
            "username": username,
            "salt": _encode(salt),
            "password_hash": _encode(_password_hash(password, salt)),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace avoids partially-written credentials; no raw password on disk.
        fd, tmp = tempfile.mkstemp(prefix=".zeus-auth-", dir=path.parent)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(record, file, sort_keys=True)
                file.write("\n")
            os.replace(tmp, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def interactive_setup(path: Path):
        path = Path(path)
        print("ZEUS Command Center: configure a LOCAL administrator account.")
        print("Credentials stay on this computer, inside your ignored data folder.")
        if path.exists():
            if input("Replace the existing login? Type REPLACE to proceed: ").strip() != "REPLACE":
                print("No changes made.")
                return False
        username = input("Username (3-32 letters/numbers/-/_): ").strip()
        password = getpass("New password (at least 12 characters): ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            raise ValueError("Passwords do not match.")
        LocalAuth.write_credentials(path, username, password)
        print("Login configured locally. Restart ZEUS to apply changes.")
        print("Do not upload data/security/auth.json or .env to GitHub.")
        return True

    def verify(self, username: str, password: str) -> bool:
        if not isinstance(username, str) or not isinstance(password, str):
            return False
        if len(username) > 32 or len(password) > 256 or not password:
            return False
        candidate = _password_hash(password, self.salt)
        return hmac.compare_digest(candidate, self.password_hash) and username == self.username

    def is_limited(self, now: float | None = None) -> bool:
        now = time() if now is None else now
        return now < self.blocked_until

    def fail(self, now: float | None = None):
        now = time() if now is None else now
        self.failures.append(now)
        while self.failures and self.failures[0] < now - LOCKOUT_WINDOW_SECONDS:
            self.failures.popleft()
        if len(self.failures) >= MAX_FAILURES:
            self.blocked_until = now + LOCKOUT_SECONDS
            self.failures.clear()

    def successful_login(self):
        self.failures.clear()
        self.blocked_until = 0

    def issue_session(self, now: float | None = None) -> str:
        now = time() if now is None else now
        self._prune(now)
        token = _encode(secrets.token_bytes(32))
        self.sessions[hashlib.sha256(token.encode("ascii")).hexdigest()] = now + SESSION_SECONDS
        # Bound in-memory sessions, without writing cookies to disk.
        if len(self.sessions) > 128:
            oldest = min(self.sessions, key=self.sessions.get)
            self.sessions.pop(oldest, None)
        return token

    def is_authenticated(self, token: str | None, now: float | None = None) -> bool:
        if not token or not isinstance(token, str) or len(token) > 128:
            return False
        now = time() if now is None else now
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expiry = self.sessions.get(digest)
        if expiry is None or expiry <= now:
            self.sessions.pop(digest, None)
            return False
        return True

    def revoke(self, token: str | None):
        if token and isinstance(token, str):
            self.sessions.pop(hashlib.sha256(token.encode("utf-8")).hexdigest(), None)

    def _prune(self, now: float):
        for key, expiry in list(self.sessions.items()):
            if expiry <= now:
                self.sessions.pop(key, None)
