"""Minimal single-Owner authentication for the HTTP API.

The deployment intentionally has one Owner and no user database. A password and
signing secret come from the environment. Successful login issues a short-lived,
HttpOnly, SameSite=Strict cookie containing a signed session. Mutating requests
must also present the CSRF token bound to that session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from stocks.settings import Settings

SESSION_COOKIE = "stocks_owner_session"
SESSION_VERSION = 1
MAX_TOKEN_LENGTH = 4096
CLOCK_SKEW_SECONDS = 30
MAX_PASSWORD_ATTEMPTS = 10
PASSWORD_ATTEMPT_WINDOW_SECONDS = 60.0


class AuthError(RuntimeError):
    """Stable authentication failure with an HTTP-safe representation."""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict:
        return {
            "detail": {
                "code": self.code,
                "message": self.message,
            }
        }


@dataclass(frozen=True)
class OwnerSession:
    issued_at: datetime
    expires_at: datetime
    csrf_token: str


class OwnerSessionManager:
    """Issue and verify the Owner's signed session token."""

    def __init__(
        self,
        password: str,
        signing_secret: str,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not password:
            raise ValueError("Owner password must not be empty")
        if len(signing_secret) < 32:
            raise ValueError("Owner session secret must contain at least 32 characters")
        if not 300 <= ttl_seconds <= 604800:
            raise ValueError("Owner session TTL must be between 300 and 604800 seconds")
        self._password = password
        self._secret = signing_secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failed_logins: OrderedDict[str, list[float]] = OrderedDict()
        self._owner_fingerprint = hmac.new(
            self._secret,
            b"owner-password:" + password.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def from_settings(cls, config: Settings) -> OwnerSessionManager:
        if config.owner_password is None or config.owner_session_secret is None:
            raise AuthError(
                "OWNER_AUTH_UNAVAILABLE",
                "Owner authentication is not configured on this server",
                status_code=503,
            )
        return cls(
            config.owner_password.get_secret_value(),
            config.owner_session_secret.get_secret_value(),
            ttl_seconds=config.owner_session_ttl_seconds,
        )

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def verify_password(self, candidate: str) -> bool:
        return hmac.compare_digest(
            self._password.encode("utf-8"), candidate.encode("utf-8")
        )

    def login_allowed(self, client_key: str) -> bool:
        """Bound repeated password guesses per client and process."""

        with self._lock:
            now = self._clock()
            attempts = self._recent_failed_logins(client_key, now)
            return len(attempts) < MAX_PASSWORD_ATTEMPTS

    def retry_after_seconds(self, client_key: str) -> int:
        """Seconds until the oldest recent failure leaves the window."""

        with self._lock:
            now = self._clock()
            attempts = self._recent_failed_logins(client_key, now)
            if len(attempts) < MAX_PASSWORD_ATTEMPTS:
                return 0
            return max(
                1,
                int(PASSWORD_ATTEMPT_WINDOW_SECONDS - (now - attempts[0])) + 1,
            )

    def record_failed_login(self, client_key: str) -> None:
        with self._lock:
            now = self._clock()
            attempts = self._recent_failed_logins(client_key, now)
            attempts.append(now)
            self._failed_logins[client_key] = attempts
            while len(self._failed_logins) > 1024:
                self._failed_logins.popitem(last=False)

    def clear_failed_logins(self, client_key: str) -> None:
        with self._lock:
            self._failed_logins.pop(client_key, None)

    def _recent_failed_logins(
        self, client_key: str, now: float
    ) -> list[float]:
        attempts = [
            attempt
            for attempt in self._failed_logins.get(client_key, [])
            if now - attempt < PASSWORD_ATTEMPT_WINDOW_SECONDS
        ]
        if attempts:
            self._failed_logins[client_key] = attempts
            self._failed_logins.move_to_end(client_key)
        else:
            self._failed_logins.pop(client_key, None)
        return attempts

    def issue(self) -> tuple[str, OwnerSession]:
        issued = int(self._clock())
        expires = issued + self._ttl_seconds
        payload = {
            "v": SESSION_VERSION,
            "iat": issued,
            "exp": expires,
            "owner": self._owner_fingerprint,
            "nonce": secrets.token_urlsafe(18),
            "csrf": secrets.token_urlsafe(32),
        }
        encoded = _encode_json(payload)
        signature = hmac.new(
            self._secret, encoded.encode("ascii"), hashlib.sha256
        ).digest()
        session = OwnerSession(
            issued_at=datetime.fromtimestamp(issued, UTC),
            expires_at=datetime.fromtimestamp(expires, UTC),
            csrf_token=payload["csrf"],
        )
        return f"{encoded}.{_b64encode(signature)}", session

    def verify(self, token: str) -> OwnerSession:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise AuthError(
                "OWNER_AUTH_REQUIRED",
                "A valid Owner session is required",
                status_code=401,
            )
        try:
            encoded, signature = token.split(".", 1)
            supplied = _b64decode(signature)
            expected = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).digest()
        except (ValueError, UnicodeError) as exc:
            raise _invalid_session() from exc
        if not hmac.compare_digest(expected, supplied):
            raise _invalid_session()
        try:
            payload = json.loads(_b64decode_text(encoded))
        except (ValueError, UnicodeError) as exc:
            raise _invalid_session() from exc
        if not isinstance(payload, dict):
            raise _invalid_session()
        nonce = payload.get("nonce")
        issued = payload.get("iat")
        expires = payload.get("exp")
        csrf = payload.get("csrf")
        now = int(self._clock())
        if (
            payload.get("v") != SESSION_VERSION
            or isinstance(issued, bool)
            or not isinstance(issued, int)
            or isinstance(expires, bool)
            or not isinstance(expires, int)
            or not isinstance(nonce, str)
            or len(nonce) < 24
            or len(nonce) > 64
            or not isinstance(csrf, str)
            or len(csrf) < 32
            or not hmac.compare_digest(
                str(payload.get("owner", "")), self._owner_fingerprint
            )
            or issued > now + CLOCK_SKEW_SECONDS
            or expires <= issued
            or expires - issued > self._ttl_seconds
        ):
            raise _invalid_session()
        if now >= expires:
            raise AuthError(
                "OWNER_SESSION_EXPIRED",
                "The Owner session has expired",
                status_code=401,
            )
        return OwnerSession(
            issued_at=datetime.fromtimestamp(issued, UTC),
            expires_at=datetime.fromtimestamp(expires, UTC),
            csrf_token=csrf,
        )


def _invalid_session() -> AuthError:
    return AuthError(
        "OWNER_SESSION_INVALID",
        "The Owner session is invalid",
        status_code=401,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _b64decode_text(value: str) -> str:
    return _b64decode(value).decode("utf-8")


def _encode_json(value: dict) -> str:
    return _b64encode(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
