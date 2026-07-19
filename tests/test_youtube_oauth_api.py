import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException

from app.api.youtube_router import google_callback, google_login
from app.config import Settings
from app.youtube.oauth import GoogleOAuth

TOKEN_JSON = {
    "access_token": "ya29.token",
    "refresh_token": "1//refresh",
    "expires_in": 3599,
    "scope": "https://www.googleapis.com/auth/youtube.upload",
    "token_type": "Bearer",
}

FRONTEND = "http://localhost:5173/oauth/callback"


def _oauth(handler, configured: bool = True) -> GoogleOAuth:
    settings = Settings(
        google_client_id="cid" if configured else "",
        google_client_secret="csecret" if configured else "",
        frontend_oauth_redirect=FRONTEND,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOAuth(settings, client)


def _request(oauth: GoogleOAuth) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(oauth=oauth, settings=oauth._settings)
        )
    )


def _ok_token_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "oauth2.googleapis.com"
    body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
    assert body["grant_type"] == "authorization_code"
    assert body["client_id"] == "cid"
    return httpx.Response(200, json=TOKEN_JSON)


def _fragment(response) -> dict:
    parsed = urlsplit(response.headers["location"])
    return {k: v[0] for k, v in parse_qs(parsed.fragment).items()}


class GoogleLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirects_to_google_consent(self) -> None:
        oauth = _oauth(_ok_token_handler)
        response = await google_login(_request(oauth), redirect=True)
        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        self.assertTrue(location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("client_id=cid", location)

    async def test_redirect_false_returns_auth_url_json(self) -> None:
        oauth = _oauth(_ok_token_handler)
        response = await google_login(_request(oauth), redirect=False)
        payload = json.loads(response.body)
        self.assertIn("state=", payload["auth_url"])

    async def test_mode_web_rides_in_state(self) -> None:
        oauth = _oauth(_ok_token_handler)
        response = await google_login(_request(oauth), redirect=False, mode="web")
        payload = json.loads(response.body)
        state = parse_qs(urlsplit(payload["auth_url"]).query)["state"][0]
        self.assertEqual(oauth.verify_state(state), "web")

    async def test_unconfigured_returns_500(self) -> None:
        oauth = _oauth(_ok_token_handler, configured=False)
        with self.assertRaises(HTTPException) as caught:
            await google_login(_request(oauth), redirect=True)
        self.assertEqual(caught.exception.status_code, 500)


class GoogleCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_returns_token_json(self) -> None:
        oauth = _oauth(_ok_token_handler)
        state = oauth.make_state()
        tokens = await google_callback(_request(oauth), code="4/authcode", state=state)
        self.assertEqual(tokens, TOKEN_JSON)

    async def test_google_error_param_returns_400(self) -> None:
        oauth = _oauth(_ok_token_handler)
        with self.assertRaises(HTTPException) as caught:
            await google_callback(
                _request(oauth), error="access_denied", state=oauth.make_state()
            )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("access_denied", caught.exception.detail)

    async def test_missing_code_returns_400(self) -> None:
        oauth = _oauth(_ok_token_handler)
        with self.assertRaises(HTTPException) as caught:
            await google_callback(_request(oauth), state=oauth.make_state())
        self.assertEqual(caught.exception.status_code, 400)

    async def test_bad_state_returns_400(self) -> None:
        oauth = _oauth(_ok_token_handler)
        with self.assertRaises(HTTPException) as caught:
            await google_callback(_request(oauth), code="4/authcode", state="forged.state.mode.sig")
        self.assertEqual(caught.exception.status_code, 400)

    async def test_missing_state_returns_400(self) -> None:
        oauth = _oauth(_ok_token_handler)
        with self.assertRaises(HTTPException) as caught:
            await google_callback(_request(oauth), code="4/authcode")
        self.assertEqual(caught.exception.status_code, 400)

    async def test_google_exchange_failure_returns_502(self) -> None:
        def failing(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Bad code"}
            )

        oauth = _oauth(failing)
        with self.assertRaises(HTTPException) as caught:
            await google_callback(
                _request(oauth), code="4/expired", state=oauth.make_state()
            )
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("invalid_grant", caught.exception.detail)

    async def test_web_mode_redirects_with_token_fragment(self) -> None:
        oauth = _oauth(_ok_token_handler)
        response = await google_callback(
            _request(oauth), code="4/authcode", state=oauth.make_state("web")
        )
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["location"].startswith(f"{FRONTEND}#"))
        fragment = _fragment(response)
        self.assertEqual(fragment["access_token"], TOKEN_JSON["access_token"])
        self.assertEqual(fragment["refresh_token"], TOKEN_JSON["refresh_token"])
        self.assertEqual(fragment["expires_in"], str(TOKEN_JSON["expires_in"]))

    async def test_web_mode_redirects_errors_to_frontend(self) -> None:
        oauth = _oauth(_ok_token_handler)
        response = await google_callback(
            _request(oauth), error="access_denied", state=oauth.make_state("web")
        )
        self.assertEqual(response.status_code, 307)
        self.assertIn("access_denied", _fragment(response)["error"])

    async def test_web_mode_exchange_failure_redirects_to_frontend(self) -> None:
        def failing(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Bad code"}
            )

        oauth = _oauth(failing)
        response = await google_callback(
            _request(oauth), code="4/expired", state=oauth.make_state("web")
        )
        self.assertEqual(response.status_code, 307)
        self.assertIn("invalid_grant", _fragment(response)["error"])


if __name__ == "__main__":
    unittest.main()
