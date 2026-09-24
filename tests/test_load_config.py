"""Tests for the env-first Config module.

`_load_config()` is the only reader of BARK_BASE / BARK_ENCRYPTION_KEY /
BARK_ENCRYPTION_IV; everything downstream receives values by injection.
"""
import contextlib
import io
import os
import tempfile
import unittest

from bark_notification import _load_config

_KEYS = ("BARK_BASE", "BARK_ENCRYPTION_KEY", "BARK_ENCRYPTION_IV")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in _KEYS:
            os.environ.pop(k, None)
        # Pin the config file to a nonexistent path so these env-only
        # tests are hermetic even on machines that have a real config.
        import bark_notification as mod
        import tempfile
        self._orig_file = mod._CONFIG_FILE
        mod._CONFIG_FILE = os.path.join(
            tempfile.gettempdir(), "bark-config-absent-test"
        )

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import bark_notification as mod
        mod._CONFIG_FILE = self._orig_file

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


class ConfigFileFallbackTests(unittest.TestCase):
    """File fallback: env > ~/.config/bark-notification/config > unset.

    The file is the single source of truth for all agents — including
    GUI-launched ones (ZCode) that cannot inherit shell env.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in _KEYS:
            os.environ.pop(k, None)
        import bark_notification as mod
        self._orig_file = mod._CONFIG_FILE
        self._tmp = tempfile.mkdtemp(prefix="bark-config-test-")
        self._file = os.path.join(self._tmp, "config")
        mod._CONFIG_FILE = self._file

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import bark_notification as mod
        mod._CONFIG_FILE = self._orig_file

    def _write(self, text):
        with open(self._file, "w") as f:
            f.write(text)

    def test_file_provides_bark_base(self):
        self._write("BARK_BASE=https://api.day.app/from-file\n")
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/from-file")

    def test_env_overrides_file(self):
        self._write("BARK_BASE=https://api.day.app/from-file\n")
        os.environ["BARK_BASE"] = "https://api.day.app/from-env"
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/from-env")

    def test_encryption_values_from_file(self):
        self._write(
            "BARK_BASE=https://api.day.app/x\n"
            "BARK_ENCRYPTION_KEY=" + "k" * 32 + "\n"
            "BARK_ENCRYPTION_IV=" + "i" * 12 + "\n"
        )
        cfg = _load_config()
        self.assertEqual(cfg.encryption_key, "k" * 32)
        self.assertEqual(cfg.encryption_iv, "i" * 12)

    def test_missing_file_is_silent(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = _load_config()
        self.assertIsNone(cfg.bark_url)
        # Only the BARK_BASE hint; no file-parse noise
        self.assertNotIn("ignoring", buf.getvalue())
        self.assertIn("BARK_BASE", buf.getvalue())

    def test_comments_and_blank_lines_ignored(self):
        self._write(
            "# comment\n\n   \nBARK_BASE=https://api.day.app/x\n## another\n"
        )
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/x")

    def test_malformed_line_warns_without_content_and_valid_lines_still_read(self):
        self._write("BARK_BASE=https://api.day.app/x\nthis-line-has-no-equals\n")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/x")
        self.assertIn(":2:", buf.getvalue())
        # The line content must NOT be echoed — it may hold a device key
        self.assertNotIn("this-line-has-no-equals", buf.getvalue())

    def test_env_empty_string_falls_through_to_file(self):
        self._write("BARK_BASE=https://api.day.app/from-file\n")
        os.environ["BARK_BASE"] = ""
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/from-file")

    def test_value_containing_equals_preserved(self):
        self._write("BARK_BASE=https://api.day.app/k?a=b==c\n")
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/k?a=b==c")

    def test_surrounding_quotes_stripped(self):
        """Values pasted from shell-export habits may carry quotes
        and stray whitespace — tolerate them instead of silently
        producing wrong-length keys (the author hit this in 5 min)."""
        self._write(
            'BARK_BASE= "https://api.day.app/x" \n'
            "BARK_ENCRYPTION_KEY='" + "k" * 32 + "'\n"
            'BARK_ENCRYPTION_IV="' + "i" * 12 + '"\n'
        )
        cfg = _load_config()
        self.assertEqual(cfg.bark_url, "https://api.day.app/x")
        self.assertEqual(cfg.encryption_key, "k" * 32)
        self.assertEqual(cfg.encryption_iv, "i" * 12)

    def test_non_utf8_file_degrades_not_crashes(self):
        with open(self._file, "wb") as f:
            f.write(b"BARK_BASE=https://api.day.app/x\n\xff\xfe")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = _load_config()
        # Good lines survive; the undecodable line is skipped, no crash
        self.assertEqual(cfg.bark_url, "https://api.day.app/x")
        self.assertIn(":2:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
