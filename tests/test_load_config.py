"""Tests for the env-first Config module.

`_load_config()` is the only reader of BARK_BASE / BARK_ENCRYPTION_KEY /
BARK_ENCRYPTION_IV; everything downstream receives values by injection.
"""
import contextlib
import io
import os
import unittest

from bark_notification import _load_config

_KEYS = ("BARK_BASE", "BARK_ENCRYPTION_KEY", "BARK_ENCRYPTION_IV")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in _KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_reads_all_values_from_env(self):
        os.environ["BARK_BASE"] = "https://api.day.app/k"
        os.environ["BARK_ENCRYPTION_KEY"] = "k" * 32
        os.environ["BARK_ENCRYPTION_IV"] = "i" * 12
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/k")
        self.assertEqual(cfg.encryption_key, "k" * 32)
        self.assertEqual(cfg.encryption_iv, "i" * 12)

    def test_missing_bark_base_disables_push_with_hint(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = _load_config()
        self.assertIsNone(cfg.bark_url)
        self.assertIn("BARK_BASE", buf.getvalue())
        self.assertIn("macOS", buf.getvalue())  # degradation is stated
        self.assertEqual(cfg.encryption_key, "")
        self.assertEqual(cfg.encryption_iv, "")

    def test_empty_bark_base_treated_as_unset(self):
        os.environ["BARK_BASE"] = ""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = _load_config()
        self.assertIsNone(cfg.bark_url)
        self.assertIn("BARK_BASE", buf.getvalue())

    def test_encryption_defaults_to_empty_when_unset(self):
        os.environ["BARK_BASE"] = "https://api.day.app/k"
        cfg = _load_config()
        self.assertEqual(cfg.encryption_key, "")
        self.assertEqual(cfg.encryption_iv, "")


if __name__ == "__main__":
    unittest.main()
