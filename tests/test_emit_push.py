"""Tests for _emit_push — the single emit seam.

_emit_push absorbs config load, cipher, form encoding, and both
notification channels. Tests inject fakes for the channels and pin the
encryption env off so the cipher is deterministically PlainForm.
"""
import os
import unittest

from bark_notification import _emit_push

_ENV_KEYS = ("BARK_BASE", "BARK_ENCRYPTION_KEY", "BARK_ENCRYPTION_IV")


class FakeChannel:
    def __init__(self):
        self.sent = []

    def send(self, form_body):
        self.sent.append(form_body)


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, title, subtitle, message):
        self.calls.append((title, subtitle, message))


class EmitPushTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        # Pin the config file to a nonexistent path: the machine's real
        # config carries encryption values that would flip the cipher.
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

    @staticmethod
    def _push():
        return {
            "title": "T",
            "markdown": "M",
            "icon": "I",
            "action": "none",
            "subtitle": "S",
        }

    def test_sends_form_to_channel_and_fields_to_notifier(self):
        ch, no = FakeChannel(), FakeNotifier()
        _emit_push(self._push(), channel=ch, notifier=no)
        self.assertEqual(len(ch.sent), 1)
        self.assertIn("title=T", ch.sent[0])
        self.assertIn("subtitle=S", ch.sent[0])
        self.assertEqual(no.calls, [("T", "S", "M")])

    def test_missing_fields_degrade_to_defaults(self):
        ch, no = FakeChannel(), FakeNotifier()
        _emit_push({"title": "Only title"}, channel=ch, notifier=no)
        self.assertEqual(no.calls, [("Only title", None, "")])

    def test_cipher_failure_skips_both_channels(self):
        """body() failure (stubbed cipher) skips both channels — the
        seam swallows transport assembly errors, same contract
        BarkChannel has for network errors."""
        from unittest import mock
        import bark_notification as mod

        class BrokenCipher:
            def body(self):
                raise RuntimeError("boom")

        ch, no = FakeChannel(), FakeNotifier()
        with mock.patch.object(
            mod.BarkCipher, "from_config", return_value=BrokenCipher()
        ):
            _emit_push(self._push(), channel=ch, notifier=no)
        self.assertEqual(ch.sent, [])
        self.assertEqual(no.calls, [])


if __name__ == "__main__":
    unittest.main()
