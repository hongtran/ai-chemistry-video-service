import time
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.auth_router import login
from app.api.schemas import LoginRequest
from app.auth import AdminAuth, require_admin
from app.config import Settings


def _auth(**overrides) -> AdminAuth:
    settings = Settings(
        admin_username="admin",
        admin_password="s3cret",
        **overrides,
    )
    return AdminAuth(settings)


def _request(auth: AdminAuth, headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(auth=auth, settings=auth._settings)),
        headers=headers or {},
    )


class AdminTokenTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        auth = _auth()
        self.assertTrue(auth.verify_token(auth.make_token()))

    def test_tampered_signature_rejected(self) -> None:
        auth = _auth()
        ts, nonce, sig = auth.make_token().split(".")
        bad_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.assertFalse(auth.verify_token(f"{ts}.{nonce}.{bad_sig}"))

    def test_tampered_payload_rejected(self) -> None:
        auth = _auth()
        ts, _, sig = auth.make_token().split(".")
        self.assertFalse(auth.verify_token(f"{ts}.other-nonce.{sig}"))

    def test_expired_token_rejected(self) -> None:
        auth = _auth(admin_session_ttl_seconds=60)
        old_ts = str(int(time.time()) - 120)
        sig = auth._sign(old_ts, "nonce")
        self.assertFalse(auth.verify_token(f"{old_ts}.nonce.{sig}"))

    def test_future_timestamp_rejected(self) -> None:
        auth = _auth()
        future_ts = str(int(time.time()) + 3600)
        sig = auth._sign(future_ts, "nonce")
        self.assertFalse(auth.verify_token(f"{future_ts}.nonce.{sig}"))

    def test_malformed_token_rejected(self) -> None:
        auth = _auth()
        for bad in ("", "a", "a.b", "a.b.c.d", "notatime.nonce." + auth._sign("notatime", "nonce")):
            self.assertFalse(auth.verify_token(bad), bad)

    def test_wrong_secret_rejected(self) -> None:
        signer = _auth(auth_secret="dedicated")
        verifier_wrong = _auth()  # falls back to admin_password
        token = signer.make_token()
        self.assertTrue(signer.verify_token(token))
        self.assertFalse(verifier_wrong.verify_token(token))

    def test_check_credentials(self) -> None:
        auth = _auth()
        self.assertTrue(auth.check_credentials("admin", "s3cret"))
        self.assertFalse(auth.check_credentials("admin", "wrong"))
        self.assertFalse(auth.check_credentials("other", "s3cret"))

    def test_enabled_requires_both_fields(self) -> None:
        self.assertTrue(_auth().enabled)
        self.assertFalse(AdminAuth(Settings(admin_username="admin", admin_password="")).enabled)
        self.assertFalse(AdminAuth(Settings(admin_username="", admin_password="x")).enabled)


class RequireAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_noop_when_disabled(self) -> None:
        auth = AdminAuth(Settings(admin_username="", admin_password=""))
        await require_admin(_request(auth))  # no exception

    async def test_noop_when_state_missing_auth(self) -> None:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={})
        await require_admin(request)  # no exception

    async def test_missing_header_rejected_with_realm(self) -> None:
        auth = _auth()
        with self.assertRaises(HTTPException) as caught:
            await require_admin(_request(auth))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(
            caught.exception.headers["WWW-Authenticate"], 'Bearer realm="admin"'
        )

    async def test_wrong_scheme_rejected(self) -> None:
        auth = _auth()
        token = auth.make_token()
        with self.assertRaises(HTTPException):
            await require_admin(_request(auth, {"authorization": f"Basic {token}"}))

    async def test_bad_token_rejected(self) -> None:
        auth = _auth()
        with self.assertRaises(HTTPException):
            await require_admin(_request(auth, {"authorization": "Bearer forged.token.sig"}))

    async def test_valid_token_passes(self) -> None:
        auth = _auth()
        token = auth.make_token()
        await require_admin(_request(auth, {"authorization": f"Bearer {token}"}))


class LoginEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_credentials_return_verifiable_token(self) -> None:
        auth = _auth(admin_session_ttl_seconds=1234)
        response = await login(
            LoginRequest(username="admin", password="s3cret"), _request(auth)
        )
        self.assertTrue(auth.verify_token(response.token))
        self.assertEqual(response.token_type, "bearer")
        self.assertEqual(response.expires_in, 1234)

    async def test_wrong_password_returns_401_without_realm(self) -> None:
        auth = _auth()
        with self.assertRaises(HTTPException) as caught:
            await login(LoginRequest(username="admin", password="wrong"), _request(auth))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIsNone(caught.exception.headers)

    async def test_wrong_username_returns_401(self) -> None:
        auth = _auth()
        with self.assertRaises(HTTPException) as caught:
            await login(LoginRequest(username="other", password="s3cret"), _request(auth))
        self.assertEqual(caught.exception.status_code, 401)

    async def test_disabled_returns_501(self) -> None:
        auth = AdminAuth(Settings(admin_username="", admin_password=""))
        with self.assertRaises(HTTPException) as caught:
            await login(LoginRequest(username="x", password="x"), _request(auth))
        self.assertEqual(caught.exception.status_code, 501)


if __name__ == "__main__":
    unittest.main()
