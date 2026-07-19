import time
import unittest

import httpx

from app.config import Settings
from app.youtube.oauth import GoogleOAuth


def _oauth(**overrides) -> GoogleOAuth:
    settings = Settings(
        google_client_id="cid",
        google_client_secret="csecret",
        **overrides,
    )
    return GoogleOAuth(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))


class OAuthStateTests(unittest.TestCase):
    def test_round_trip_returns_mode(self) -> None:
        oauth = _oauth()
        self.assertEqual(oauth.verify_state(oauth.make_state()), "json")
        self.assertEqual(oauth.verify_state(oauth.make_state("web")), "web")

    def test_tampered_signature_rejected(self) -> None:
        oauth = _oauth()
        ts, nonce, mode, sig = oauth.make_state().split(".")
        bad_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.assertIsNone(oauth.verify_state(f"{ts}.{nonce}.{mode}.{bad_sig}"))

    def test_tampered_payload_rejected(self) -> None:
        oauth = _oauth()
        ts, _, mode, sig = oauth.make_state().split(".")
        self.assertIsNone(oauth.verify_state(f"{ts}.other-nonce.{mode}.{sig}"))

    def test_tampered_mode_rejected(self) -> None:
        oauth = _oauth()
        ts, nonce, _, sig = oauth.make_state("json").split(".")
        self.assertIsNone(oauth.verify_state(f"{ts}.{nonce}.web.{sig}"))

    def test_expired_state_rejected(self) -> None:
        oauth = _oauth(oauth_state_max_age_seconds=60)
        old_ts = str(int(time.time()) - 120)
        sig = oauth._sign(old_ts, "nonce", "json")
        self.assertIsNone(oauth.verify_state(f"{old_ts}.nonce.json.{sig}"))

    def test_future_timestamp_rejected(self) -> None:
        oauth = _oauth()
        future_ts = str(int(time.time()) + 3600)
        sig = oauth._sign(future_ts, "nonce", "json")
        self.assertIsNone(oauth.verify_state(f"{future_ts}.nonce.json.{sig}"))

    def test_malformed_state_rejected(self) -> None:
        oauth = _oauth()
        for bad in ("", "a", "a.b", "a.b.c", "a.b.c.d.e", "notatime.nonce.json." + oauth._sign("notatime", "nonce", "json")):
            self.assertIsNone(oauth.verify_state(bad), bad)

    def test_state_secret_overrides_client_secret(self) -> None:
        signer = _oauth(oauth_state_secret="dedicated")
        verifier_wrong = _oauth()  # falls back to client secret
        state = signer.make_state()
        self.assertEqual(signer.verify_state(state), "json")
        self.assertIsNone(verifier_wrong.verify_state(state))

    def test_auth_url_carries_params(self) -> None:
        oauth = _oauth()
        state = oauth.make_state()
        url = oauth.build_auth_url(state)
        self.assertIn("client_id=cid", url)
        self.assertIn("youtube.upload", url)
        self.assertIn("access_type=offline", url)
        self.assertIn(f"state={state}", url)


if __name__ == "__main__":
    unittest.main()
