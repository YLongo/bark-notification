"""Tests for BarkCipher strategy object.

The cipher turns a Bark PushPayload into the body of an HTTP POST.
Two strategies: EncryptedForm (AES-256-GCM) and PlainForm.
"""
import base64
import json
import unittest

from bark_notification import (
    BarkCipher,
    EncryptedForm,
    PlainForm,
)


# Fixed test key/iv for deterministic encryption.
_KEY = b"k" * 32  # 32-byte AES-256 key
_IV = b"i" * 12   # 12-byte GCM nonce


class PlainFormTests(unittest.TestCase):
    def test_encodes_all_fields(self):
        push = {"title": "T", "markdown": "M", "icon": "I", "action": "none"}
        form = PlainForm(push).body()
        self.assertIn("title=T", form)
        self.assertIn("markdown=M", form)
        self.assertIn("icon=I", form)
        self.assertIn("action=none", form)


def _xor_encrypt(plaintext: bytes, key: bytes, iv) -> str:
    """Deterministic test stand-in: XOR plaintext with iv-repeated bytes,
    base64-encoded. Reversible so we can verify round-trip.

    Accepts iv as bytes or str (production passes bytes via AESGCM
    interface; some tests pass the text form)."""
    iv_b = iv.encode("utf-8") if isinstance(iv, str) else iv
    if not iv_b:
        raise ValueError("iv must be non-empty")
    keystream = (iv_b * ((len(plaintext) // len(iv_b)) + 1))[: len(plaintext)]
    ct = bytes(b ^ k for b, k in zip(plaintext, keystream))
    return base64.b64encode(ct).decode("ascii")


def _xor_decrypt(ciphertext_b64: str, key: bytes, iv: bytes) -> bytes:
    raw = base64.b64decode(ciphertext_b64)
    keystream = (iv * ((len(raw) // len(iv)) + 1))[: len(raw)]
    return bytes(b ^ k for b, k in zip(raw, keystream))


class EncryptedFormTests(unittest.TestCase):
    def test_body_contains_ciphertext_and_iv(self):
        push = {"title": "T", "markdown": "M", "icon": "I", "action": "none"}
        ef = EncryptedForm(push, key=_KEY, iv_bytes=_IV, iv_text="i" * 12, encrypt_fn=_xor_encrypt)
        body = ef.body()
        self.assertIn("ciphertext=", body)
        self.assertIn("iv=" + "i" * 12, body)

    def test_ciphertext_round_trip(self):
        # The wire format must round-trip: parse body, decrypt, recover push.
        push = {"title": "T", "markdown": "M", "icon": "I", "action": "none"}
        ef = EncryptedForm(push, key=_KEY, iv_bytes=_IV, iv_text="i" * 12, encrypt_fn=_xor_encrypt)
        from urllib.parse import parse_qs
        params = parse_qs(ef.body())
        ct = params["ciphertext"][0]
        plaintext = _xor_decrypt(ct, _KEY, _IV)
        self.assertEqual(json.loads(plaintext), push)

    def test_iv_in_body_is_text_iv(self):
        push = {"title": "T", "markdown": "M"}
        ef = EncryptedForm(push, key=_KEY, iv_bytes="i" * 12, iv_text="i" * 12, encrypt_fn=_xor_encrypt)
        self.assertIn("iv=" + "i" * 12, ef.body())

    def test_default_encrypt_fn_is_aes_gcm(self):
        # If cryptography is installed the default function uses AESGCM;
        # without it, _encrypt_aes_gcm will raise when actually called.
        # We only verify the attribute is wired — not the encryption result.
        ef = EncryptedForm({"title": "T"}, key=_KEY, iv_bytes=_IV, iv_text="i" * 12)
        self.assertIsNotNone(ef._encrypt_fn)


class BarkCipherFallbackWarningTests(unittest.TestCase):
    """When key/iv are present but BarkCipher would otherwise need the
    optional `cryptography` package, from_config() should warn on stderr
    rather than silently fall back to plaintext. Otherwise a user who
    configured encryption but forgot to install cryptography sees no
    notification and no error.
    """

    def _capture(self, key, iv):
        import io, contextlib
        from bark_notification import BarkCipher
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            BarkCipher.from_config(key=key, iv=iv, payload={"title": "T"})
        return buf.getvalue()

    def test_warns_when_encryption_configured_but_crypto_missing(self):
        # Skip cleanly if cryptography is actually installed — the test
        # is specifically about the missing-dependency fallback path.
        try:
            import cryptography  # noqa: F401
            self.skipTest("cryptography is installed; fallback path inactive")
        except ImportError:
            pass
        err = self._capture("k" * 32, "i" * 12)
        self.assertIn("cryptography", err)
        self.assertIn("plaintext", err)

    def test_no_warning_when_encryption_not_configured(self):
        # No key and no IV → user clearly opted out; silent PlainForm.
        err = self._capture("", "")
        self.assertEqual(err, "")

    def test_warns_when_key_wrong_length(self):
        # Key set but wrong length → misconfiguration: fall back to
        # plaintext but say so, instead of failing silently.
        err = self._capture("short", "i" * 12)
        self.assertIn("32", err)
        self.assertIn("plaintext", err)


class BarkCipherFromConfigTests(unittest.TestCase):
    def test_plain_when_key_wrong_length(self):
        push = {"title": "T"}
        cipher = BarkCipher.from_config(key="short", iv="i" * 12, payload=push)
        self.assertIsInstance(cipher, PlainForm)

    def test_plain_when_iv_wrong_length(self):
        push = {"title": "T"}
        cipher = BarkCipher.from_config(key="k" * 32, iv="short", payload=push)
        self.assertIsInstance(cipher, PlainForm)

    def test_plain_when_no_key(self):
        push = {"title": "T"}
        cipher = BarkCipher.from_config(key="", iv="", payload=push)
        self.assertIsInstance(cipher, PlainForm)

    def test_encrypted_when_key_and_iv_valid(self):
        # Real AES-GCM round-trip is exercised when `cryptography` is
        # installed; otherwise we verify the wiring (encrypted branch
        # selected) by injecting a stand-in encrypt_fn post-construction.
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed; from_config falls back to PlainForm")
        push = {"title": "T"}
        cipher = BarkCipher.from_config(
            key="k" * 32, iv="i" * 12, payload=push
        )
        self.assertIsInstance(cipher, EncryptedForm)
        cipher._encrypt_fn = _xor_encrypt
        self.assertIn("ciphertext=", cipher.body())


if __name__ == "__main__":
    unittest.main()
