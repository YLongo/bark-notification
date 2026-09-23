"""Tests for MacOSNotifier.

MacOSNotifier.notify() shells out to `osascript` and `afplay`. Tests
inject a fake runner and fake Popen so no real subprocess is launched.
"""
import contextlib
import io
import unittest
from unittest.mock import patch

from bark_notification import MacOSNotifier


class FakeRunner:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.fail:
            raise OSError("simulated osascript failure")


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return None


class MacOSNotifierTests(unittest.TestCase):
    def test_notify_calls_osascript_with_title_and_message(self):
        runner = FakeRunner()
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            n.notify("Codex", None, "hello world")
        self.assertEqual(len(runner.calls), 1)
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(kwargs.get("capture_output"), True)
        script = argv[2]
        self.assertIn('display notification "hello world"', script)
        self.assertIn('with title "Codex"', script)
        self.assertNotIn("subtitle", script)

    def test_notify_includes_subtitle_when_provided(self):
        runner = FakeRunner()
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            n.notify("Claude", "Permission needed", "approve?")
        script = runner.calls[0][0][2]
        self.assertIn('subtitle "Permission needed"', script)

    def test_notify_plays_sound_after_osascript(self):
        runner = FakeRunner()
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            n.notify("Codex", None, "msg")
        self.assertEqual(len(popen.calls), 1)
        argv, _ = popen.calls[0]
        self.assertEqual(argv[0], "afplay")
        self.assertTrue(argv[1].endswith("Glass.aiff"))

    def test_notify_swallows_runner_errors(self):
        runner = FakeRunner(fail=True)
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            # Must not raise.
            n.notify("Codex", None, "msg")
        # Popen must not be called when osascript fails.
        self.assertEqual(popen.calls, [])

    def test_notify_logs_runner_errors_to_stderr(self):
        runner = FakeRunner(fail=True)
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                n.notify("Codex", None, "msg")
        self.assertIn("bark-notify", buf.getvalue())
        self.assertIn("macos notify failed", buf.getvalue())

    def test_notify_does_not_log_on_success(self):
        runner = FakeRunner()
        popen = FakePopen()
        with patch("bark_notification.subprocess.Popen", popen):
            n = MacOSNotifier(runner=runner)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                n.notify("Codex", None, "msg")
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
