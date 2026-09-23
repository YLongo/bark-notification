"""Tests for PushBuilder.

PushBuilder takes a raw payload and a resolved AgentSource and returns
the dict that gets shipped to Bark (after optional cipher wrapping).
"""
import unittest

from bark_notification import (
    CLAUDE_ICON_URL,
    OPENAI_ICON_URL,
    OPENCODE_ICON_URL,
    PI_ICON_URL,
    ZCODE_ICON_URL,
    PushBuilder,
)


class PushBuilderTests(unittest.TestCase):
    def test_claude_full_payload(self):
        payload = {
            "hook_event_name": "Notification",
            "title": "Build finished",
            "last-assistant-message": "Compiled in 3.2s",
        }
        out = PushBuilder.build(payload, "claude")
        self.assertEqual(out["title"], "Build finished")
        self.assertEqual(out["icon"], CLAUDE_ICON_URL)
        self.assertEqual(out["markdown"], "Compiled in 3.2s")
        self.assertEqual(out["subtitle"], "Notification")
        self.assertEqual(out["action"], "none")

    def test_claude_default_title(self):
        out = PushBuilder.build({}, "claude")
        self.assertEqual(out["title"], "Claude Code")
        self.assertEqual(out["icon"], CLAUDE_ICON_URL)

    def test_opencode_default_title(self):
        out = PushBuilder.build({}, "opencode")
        self.assertEqual(out["title"], "OpenCode")
        self.assertEqual(out["icon"], OPENCODE_ICON_URL)

    def test_codex_default_title(self):
        out = PushBuilder.build({}, "codex")
        self.assertEqual(out["title"], "Codex")
        self.assertEqual(out["icon"], OPENAI_ICON_URL)

    def test_message_prefers_last_assistant(self):
        payload = {
            "last-assistant-message": "first",
            "message": "second",
            "summary": "third",
        }
        out = PushBuilder.build(payload, "codex")
        self.assertEqual(out["markdown"], "first")

    def test_message_falls_back_to_message_field(self):
        out = PushBuilder.build({"message": "hello"}, "codex")
        self.assertEqual(out["markdown"], "hello")

    def test_message_falls_back_to_summary(self):
        out = PushBuilder.build({"summary": "summary text"}, "codex")
        self.assertEqual(out["markdown"], "summary text")

    def test_message_falls_back_to_event_and_cwd(self):
        out = PushBuilder.build(
            {"hook_event_name": "Stop", "cwd": "/repo"}, "claude"
        )
        self.assertEqual(out["markdown"], "Stop in /repo")

    def test_message_falls_back_to_event_only(self):
        out = PushBuilder.build({"type": "session.idle"}, "opencode")
        self.assertEqual(out["markdown"], "Event: session.idle")

    def test_message_falls_back_to_cwd_only(self):
        out = PushBuilder.build({"cwd": "/tmp"}, "codex")
        self.assertEqual(out["markdown"], "Event in /tmp")

    def test_pi_default_title(self):
        out = PushBuilder.build({}, "pi")
        self.assertEqual(out["title"], "Pi")
        self.assertEqual(out["icon"], PI_ICON_URL)

    def test_zcode_default_title(self):
        out = PushBuilder.build({}, "zcode")
        self.assertEqual(out["title"], "ZCode")
        self.assertEqual(out["icon"], ZCODE_ICON_URL)

    def test_zcode_custom_title(self):
        out = PushBuilder.build({"title": "ZCode GLM-5.3"}, "zcode")
        self.assertEqual(out["title"], "ZCode GLM-5.3")
        self.assertEqual(out["icon"], ZCODE_ICON_URL)

    def test_message_includes_tool_name_for_tool_events(self):
        out = PushBuilder.build(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "cwd": "/repo",
            },
            "zcode",
        )
        self.assertEqual(out["markdown"], "PermissionRequest: Bash in /repo")

    def test_message_tool_name_without_cwd(self):
        out = PushBuilder.build(
            {"hook_event_name": "PreToolUse", "tool_name": "Write"}, "zcode"
        )
        self.assertEqual(out["markdown"], "PreToolUse: Write")

    def test_pi_custom_title(self):
        out = PushBuilder.build({"title": "Pi Refactor"}, "pi")
        self.assertEqual(out["title"], "Pi Refactor")
        self.assertEqual(out["icon"], PI_ICON_URL)

    def test_message_final_fallback(self):
        out = PushBuilder.build({}, "codex")
        self.assertEqual(out["markdown"], "Event")

    def test_no_subtitle_when_no_event(self):
        out = PushBuilder.build({"message": "hi"}, "codex")
        self.assertNotIn("subtitle", out)

    def test_subtitle_from_hook_event_name(self):
        out = PushBuilder.build(
            {"hook_event_name": "PermissionRequest", "message": "x"}, "claude"
        )
        self.assertEqual(out["subtitle"], "PermissionRequest")

    def test_subtitle_from_type(self):
        out = PushBuilder.build({"type": "session.idle"}, "opencode")
        self.assertEqual(out["subtitle"], "session.idle")

    def test_subtitle_from_event(self):
        out = PushBuilder.build({"event": "permission.asked"}, "opencode")
        self.assertEqual(out["subtitle"], "permission.asked")


class PushBuilderFromPartsTests(unittest.TestCase):
    """from_parts: assemble a PushPayload from display fields (the
    Stopgate timer path — no raw payload available at fire time)."""

    def test_assembles_payload_with_icon_and_action(self):
        push = PushBuilder.from_parts("claude", "🎉 Claude Code 工作完成", "done", "Stop")
        self.assertEqual(push["title"], "🎉 Claude Code 工作完成")
        self.assertEqual(push["markdown"], "done")
        self.assertEqual(push["subtitle"], "Stop")
        self.assertEqual(push["icon"], CLAUDE_ICON_URL)
        self.assertEqual(push["action"], "none")

    def test_no_subtitle_when_absent(self):
        push = PushBuilder.from_parts("codex", "T", "M")
        self.assertNotIn("subtitle", push)

    def test_unknown_source_gets_default_icon(self):
        push = PushBuilder.from_parts("mystery", "T", "M")
        self.assertEqual(push["icon"], OPENAI_ICON_URL)

    def test_truncates_long_message(self):
        push = PushBuilder.from_parts("codex", "T", "x" * 600)
        self.assertLessEqual(len(push["markdown"]), 500)
        self.assertTrue(push["markdown"].endswith("..."))

    def test_build_truncates_long_message(self):
        out = PushBuilder.build({"message": "y" * 600}, "codex")
        self.assertLessEqual(len(out["markdown"]), 500)
        self.assertTrue(out["markdown"].endswith("..."))


class PushBuilderTitleForTests(unittest.TestCase):
    def test_known_source(self):
        self.assertEqual(PushBuilder.title_for("claude"), "Claude Code")

    def test_unknown_source_falls_back(self):
        self.assertEqual(PushBuilder.title_for("nope"), "Codex")


if __name__ == "__main__":
    unittest.main()
