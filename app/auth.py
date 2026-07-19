"""Single-admin authentication.

One account, credentials from the environment. Login mints a stateless
HMAC-signed bearer token (same ts.nonce.sig pattern as the OAuth state in
app/youtube/oauth.py) — no server-side session state. With ADMIN_USERNAME/
ADMIN_PASSWORD unset, auth is disabled and `require_admin` is a no-op.
"""
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from app.config import Settings

# Marks admin-session 401s so the SPA can tell them apart from the Google
# access-token 401 raised by the YouTube upload endpoint.
WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": 'Bearer realm="admin"'}


class AdminAuth:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.admin_username and self._settings.admin_password)

    def check_credentials(self, username: str, password: str) -> bool:
        # Compare both fields before combining so a username mismatch does not
        # short-circuit the password comparison (constant-time either way).
        user_ok = secrets.compare_digest(
            username.encode("utf-8"), self._settings.admin_username.encode("utf-8")
        )
        pass_ok = secrets.compare_digest(
            password.encode("utf-8"), self._settings.admin_password.encode("utf-8")
        )
        return user_ok and pass_ok

    def _secret(self) -> bytes:
        secret = self._settings.auth_secret or self._settings.admin_password
        return secret.encode("utf-8")

    def _sign(self, ts: str, nonce: str) -> str:
        return hmac.new(
            self._secret(), f"{ts}.{nonce}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def make_token(self) -> str:
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        return f"{ts}.{nonce}.{self._sign(ts, nonce)}"

    def verify_token(self, token: str) -> bool:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        ts, nonce, sig = parts
        if not hmac.compare_digest(sig, self._sign(ts, nonce)):
            return False
        try:
            age = time.time() - int(ts)
        except ValueError:
            return False
        return 0 <= age <= self._settings.admin_session_ttl_seconds


async def require_admin(request: Request) -> None:
    """Router dependency: enforce the admin bearer token when auth is enabled."""
    auth: AdminAuth | None = getattr(request.app.state, "auth", None)
    if auth is None or not auth.enabled:
        return
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not auth.verify_token(token.strip()):
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required.",
            headers=WWW_AUTHENTICATE_HEADER,
        )
