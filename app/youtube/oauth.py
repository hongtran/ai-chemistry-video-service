"""Google OAuth2 broker for YouTube uploads.

The server brokers the browser consent flow but stores no tokens: the callback
exchanges the authorization code and hands Google's token JSON straight back
to the client, which owns the token lifecycle from there. Because there is no
session to bind the CSRF state to, state is a signed timestamped nonce
verified at the callback (stdlib hmac only).
"""
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import httpx

from app.config import Settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
TOKENINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/tokeninfo"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


class GoogleOAuthError(Exception):
    def __init__(self, status_code: int, error: str, description: str = "") -> None:
        self.status_code = status_code
        self.error = error
        self.description = description
        super().__init__(f"{error}: {description}" if description else error)


class GoogleOAuth:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.google_client_id and self._settings.google_client_secret
        )

    def _state_secret(self) -> bytes:
        secret = self._settings.oauth_state_secret or self._settings.google_client_secret
        return secret.encode("utf-8")

    def _sign(self, ts: str, nonce: str, mode: str) -> str:
        return hmac.new(
            self._state_secret(), f"{ts}.{nonce}.{mode}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def make_state(self, mode: str = "json") -> str:
        # The response mode (json for API clients, web for the SPA redirect)
        # rides inside the MAC so the callback can trust it untampered.
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        return f"{ts}.{nonce}.{mode}.{self._sign(ts, nonce, mode)}"

    def verify_state(self, state: str) -> str | None:
        """Return the mode carried in the state, or None if invalid/expired."""
        parts = state.split(".")
        if len(parts) != 4:
            return None
        ts, nonce, mode, sig = parts
        if not hmac.compare_digest(sig, self._sign(ts, nonce, mode)):
            return None
        try:
            age = time.time() - int(ts)
        except ValueError:
            return None
        if not 0 <= age <= self._settings.oauth_state_max_age_seconds:
            return None
        return mode

    def build_auth_url(self, state: str) -> str:
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": self._settings.google_oauth_scopes,
            # offline + consent so Google also returns a refresh_token the
            # client can keep for later re-authorization.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        resp = await self._client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": self._settings.google_client_id,
                "client_secret": self._settings.google_client_secret,
                "redirect_uri": self._settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            raise GoogleOAuthError(
                resp.status_code,
                payload.get("error", "token_exchange_failed"),
                payload.get("error_description", ""),
            )
        return resp.json()

    async def verify_access_token(self, access_token: str) -> bool:
        """Fast pre-check so an expired token fails the POST with 401 instead
        of surfacing minutes later on the background upload."""
        resp = await self._client.get(
            TOKENINFO_ENDPOINT, params={"access_token": access_token}
        )
        if resp.status_code != 200:
            return False
        return UPLOAD_SCOPE in resp.json().get("scope", "").split()
