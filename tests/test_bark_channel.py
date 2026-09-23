"""Tests for BarkChannel.

BarkChannel.send() POSTs the form-encoded body to the configured Bark
URL. Tests inject a fake url opener so no real HTTP request is made.
"""
import unittest
from urllib.error import URLError

from bark_notification import BarkChannel

_URL = "https://api.day.app/test-device-key"


class FakeOpener:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, req, timeout=5):
        self.calls.append((req.full_url, req.data, req.get_header("Content-type"), timeout))
        if self.fail:
            raise URLError("simulated network failure")
        return _FakeResponse()


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"code":200,"message":"ok"}'


class BarkChannelTests(unittest.TestCase):
    def test_send_calls_opener_with_form(self):
        opener = FakeOpener()
        ch = BarkChannel(url=_URL, opener=opener)
        ch.send("ciphertext=abc&iv=xxx")
        url, data, ctype, timeout = opener.calls[0]
        self.assertEqual(url, _URL)
        self.assertEqual(data, b"ciphertext=abc&iv=xxx")
        self.assertEqual(ctype, "application/x-www-form-urlencoded")
        self.assertEqual(timeout, 5)

    def test_send_swallows_network_errors(self):
        opener = FakeOpener(fail=True)
        ch = BarkChannel(url=_URL, opener=opener)
        # Must not raise even when urllib raises URLError.
        ch.send("ciphertext=abc&iv=xxx")

    def test_send_logs_network_errors_to_stderr(self):
        import contextlib, io
        opener = FakeOpener(fail=True)
        ch = BarkChannel(url=_URL, opener=opener)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ch.send("ciphertext=abc&iv=xxx")
        self.assertIn("bark-notify", buf.getvalue())
        self.assertIn("send", buf.getvalue())

    def test_send_does_not_log_on_success(self):
        import contextlib, io
        opener = FakeOpener()
        ch = BarkChannel(url=_URL, opener=opener)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ch.send("ciphertext=abc&iv=xxx")
        self.assertEqual(buf.getvalue(), "")

    def test_send_skips_when_no_url(self):
        """url=None (BARK_BASE unset): send() is a silent no-op — the
        guidance hint is _load_config()'s job, not the channel's."""
        import contextlib, io
        opener = FakeOpener()
        ch = BarkChannel(url=None, opener=opener)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ch.send("ciphertext=abc&iv=xxx")
        self.assertEqual(opener.calls, [])
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
