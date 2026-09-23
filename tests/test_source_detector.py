"""Truth-table tests for SourceDetector.

Behavior contract: detection must remain identical to the previous
string-sniffing ladder for all known payload shapes, while gaining a
fast path for explicit `source` tagging.
"""
import os
import unittest
from unittest.mock import patch

from bark_notification import SourceDetector


class SourceDetectorTests(unittest.TestCase):
    # --- tag-first (new) ---------------------------------------------------
    def test_explicit_claude_tag(self):
        self.assertEqual(
            SourceDetector.detect({"source": "claude", "title": "OpenCode"}),
            "claude",
        )

    def test_explicit_opencode_tag(self):
        self.assertEqual(
            SourceDetector.detect({"source": "opencode"}),
            "opencode",
        )

    def test_explicit_codex_tag(self):
        self.assertEqual(SourceDetector.detect({"source": "codex"}), "codex")

    def test_unknown_tag_falls_through(self):
        # Unknown source value must not poison detection.
        self.assertEqual(
            SourceDetector.detect({"source": "mystery", "hook_event_name": "Notification"}),
            "claude",
        )

    # --- heuristic fallback (preserved from original ladder) ---------------
    def test_claude_hook_event_name(self):
        self.assertEqual(
            SourceDetector.detect({"hook_event_name": "Notification"}),
            "claude",
        )

    def test_claude_session_id(self):
        self.assertEqual(
            SourceDetector.detect({"session_id": "abc"}),
            "claude",
        )

    def test_claude_transcript_path(self):
        self.assertEqual(
            SourceDetector.detect({"transcript_path": "/tmp/x.jsonl"}),
            "claude",
        )

    def test_claude_title_substring(self):
        self.assertEqual(SourceDetector.detect({"title": "My Claude Task"}), "claude")

    def test_opencode_title_substring_capitalized(self):
        self.assertEqual(
            SourceDetector.detect({"title": "OpenCode session"}),
            "opencode",
        )

    def test_opencode_event_session_prefix(self):
        self.assertEqual(
            SourceDetector.detect({"event": "session.idle"}),
            "opencode",
        )

    def test_opencode_event_legacy_name(self):
        self.assertEqual(
            SourceDetector.detect({"type": "session_completed"}),
            "opencode",
        )

    def test_opencode_event_file_edited(self):
        self.assertEqual(
            SourceDetector.detect({"event": "file_edited"}),
            "opencode",
        )

    def test_default_is_codex(self):
        self.assertEqual(SourceDetector.detect({}), "codex")

    def test_pi_explicit_tag(self):
        self.assertEqual(
            SourceDetector.detect({"source": "pi"}),
            "pi",
        )

    def test_pi_title_substring(self):
        self.assertEqual(
            SourceDetector.detect({"title": "Pi finished"}),
            "pi",
        )

    def test_pi_tag_beats_sniff(self):
        # Explicit pi tag must win even if payload looks like Claude
        self.assertEqual(
            SourceDetector.detect(
                {"source": "pi", "hook_event_name": "Notification"}
            ),
            "pi",
        )

    def test_opencode_beats_claude_when_tagged_but_title_has_claude(self):
        # Regression for the original ladder's title-sniff precedence bug:
        # if an OpenCode payload's title mentions "Claude", the explicit tag
        # must win so the icon is still OpenCode's.
        self.assertEqual(
            SourceDetector.detect(
                {"source": "opencode", "title": "Claude plugin test"}
            ),
            "opencode",
        )


class ZCodeDetectorTests(unittest.TestCase):
    """ZCode (Z.ai) hook payloads are Claude-compatible on purpose, so
    detection must fire on ZCode-only markers BEFORE the Claude ladder.
    Env-based tests patch the environment so they never leak."""

    _ZCODE_ENV = {"ZCODE_SESSION_ID": "s-1", "ZCODE_PROJECT_DIR": "/repo"}

    def test_explicit_zcode_tag(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(SourceDetector.detect({"source": "zcode"}), "zcode")

    def test_zcode_env_vars_win_over_claude_shape(self):
        payload = {"hook_event_name": "Stop", "session_id": "s-1"}
        with patch.dict(os.environ, self._ZCODE_ENV, clear=False):
            self.assertEqual(SourceDetector.detect(payload), "zcode")

    def test_zcode_env_project_dir_alone(self):
        with patch.dict(os.environ, {"ZCODE_PROJECT_DIR": "/repo"}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            self.assertEqual(SourceDetector.detect({}), "zcode")

    def test_zcode_camelcase_duplicates(self):
        # ZCode spreads its own camelCase fields (hookEventName,
        # transcriptPath) alongside the snake_case aliases — Claude Code
        # only sends snake_case.
        payload = {
            "hookEventName": "Stop",
            "hook_event_name": "Stop",
            "sessionId": "s-1",
            "session_id": "s-1",
            "transcriptPath": "/tmp/zcode-hook-x/transcript.jsonl",
            "transcript_path": "/tmp/zcode-hook-x/transcript.jsonl",
            "last_assistant_message": "done",
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(SourceDetector.detect(payload), "zcode")

    def test_zcode_agent_type_field(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(
                SourceDetector.detect(
                    {"agent_type": "build", "hook_event_name": "SessionStart"}
                ),
                "zcode",
            )

    def test_zcode_transcript_dir_marker(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(
                SourceDetector.detect(
                    {"transcript_path": "/tmp/zcode-hook-abc/transcript.jsonl"}
                ),
                "zcode",
            )

    def test_zcode_title_substring(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(SourceDetector.detect({"title": "ZCode done"}), "zcode")

    def test_claude_payload_without_zcode_markers_still_claude(self):
        # No camelCase duplicates, no agent_type, no zcode-hook path, no
        # env vars → the classic Claude ladder must keep working.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(
                SourceDetector.detect(
                    {"hook_event_name": "Stop", "transcript_path": "/tmp/x.jsonl"}
                ),
                "claude",
            )

    def test_reasonix_camelcase_not_confused_with_zcode(self):
        # Reasonix uses sessionId/lastAssistantText but never
        # hookEventName/transcriptPath/agent_type — it must stay reasonix.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZCODE_SESSION_ID", None)
            os.environ.pop("ZCODE_PROJECT_DIR", None)
            self.assertEqual(
                SourceDetector.detect(
                    {"sessionId": "s", "lastAssistantText": "done"}
                ),
                "reasonix",
            )


if __name__ == "__main__":
    unittest.main()
