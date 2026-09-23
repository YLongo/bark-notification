"""Tests for Stop-hook suppression + Stopgate debounce state machine.

Stopgate solves two problems:
  1. `Stop` fires every turn even when Claude continues mid-task.
  2. `TaskCompleted` fires once per task; multi-task turns would spam.

Both Stop and TaskCompleted schedule a notification through a 5-second
gate. PostToolBatch (Claude resumed work) cancels the pending gate.
A newer event overwrites an older one so only the last one fires.

Implementation note: in production, schedule() forks a detached
subprocess to outlive the hook process. Tests exercise the state-file
logic directly (cancel, fire_if_ours, token-mismatch) without forking,
by writing state files and calling fire_if_ours synchronously.
"""
import json
import os
import tempfile
import time
import unittest

from bark_notification import Stopgate, _has_running_background_tasks


class HasRunningBackgroundTasksTests(unittest.TestCase):
    def test_empty_payload_is_false(self):
        self.assertFalse(_has_running_background_tasks({}))

    def test_missing_field_is_false(self):
        self.assertFalse(_has_running_background_tasks({"hook_event_name": "Stop"}))

    def test_none_is_false(self):
        self.assertFalse(_has_running_background_tasks({"background_tasks": None}))

    def test_empty_list_is_false(self):
        self.assertFalse(_has_running_background_tasks({"background_tasks": []}))

    def test_all_done_is_false(self):
        tasks = [
            {"id": "t1", "status": "completed"},
            {"id": "t2", "status": "exited"},
        ]
        self.assertFalse(_has_running_background_tasks({"background_tasks": tasks}))

    def test_one_running_is_true(self):
        tasks = [
            {"id": "t1", "status": "completed"},
            {"id": "t2", "status": "running", "command": "tail -f log"},
        ]
        self.assertTrue(_has_running_background_tasks({"background_tasks": tasks}))

    def test_all_running_is_true(self):
        tasks = [{"id": "t1", "status": "running"}]
        self.assertTrue(_has_running_background_tasks({"background_tasks": tasks}))

    def test_non_list_field_is_false(self):
        self.assertFalse(_has_running_background_tasks({"background_tasks": "running"}))
        self.assertFalse(_has_running_background_tasks({"background_tasks": {"status": "running"}}))

    def test_non_dict_entries_are_ignored(self):
        tasks = ["running", 42, None, {"status": "running"}]
        self.assertTrue(_has_running_background_tasks({"background_tasks": tasks}))

    def test_entries_without_status_are_ignored(self):
        tasks = [{"id": "t1"}, {"id": "t2", "status": "running"}]
        self.assertTrue(_has_running_background_tasks({"background_tasks": tasks}))


class StopgateTests(unittest.TestCase):
    """State-file logic tests. Firing uses a stubbed send fn by
    monkeypatching _send_notification so no real Bark/macOS call is made.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="stopgate-test-")
        self._orig_dir = Stopgate._STATE_DIR
        Stopgate._STATE_DIR = self._tmp
        # Capture fire calls instead of actually notifying.
        self._fired = []
        import bark_notification as mod
        self._orig_send = mod._send_notification
        mod._send_notification = lambda title, message, subtitle=None, agent_source="codex": self._fired.append(
            (title, message, subtitle, agent_source)
        )

    def tearDown(self):
        Stopgate._STATE_DIR = self._orig_dir
        import bark_notification as mod
        mod._send_notification = self._orig_send

    def _state_file(self, session_id):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return os.path.join(self._tmp, f"state_{safe}.json")

    def _write_state(self, session_id, token, source="stop", title="T", message="M"):
        path = self._state_file(session_id)
        with open(path, "w") as f:
            json.dump({"token": token, "source": source, "title": title,
                       "message": message, "ts": time.time()}, f)
        return path

    def test_state_file_isolated_per_session(self):
        a = Stopgate.state_file_for("sess-A")
        b = Stopgate.state_file_for("sess-B")
        self.assertNotEqual(a, b)

    def test_cancel_removes_state_file(self):
        self._write_state("s1", "tok-1")
        Stopgate.cancel("s1")
        self.assertFalse(os.path.exists(self._state_file("s1")))

    def test_cancel_missing_file_is_silent(self):
        Stopgate.cancel("never-scheduled")

    def test_fire_if_ours_sends_when_token_matches(self):
        path = self._write_state("s1", "tok-1", source="stop",
                                  title="🎉 Claude Code 工作完成", message="done")
        Stopgate.fire_if_ours(path, "tok-1", "🎉 Claude Code 工作完成", "done",
                              source="stop", delay=0, session_id="s1", agent_source="claude")
        self.assertEqual(self._fired, [("🎉 Claude Code 工作完成", "done", "Stop", "claude")])
        self.assertFalse(os.path.exists(path))

    def test_fire_if_ours_silent_when_token_mismatch(self):
        """A newer event overwrote the state file — older timer defers."""
        path = self._write_state("s1", "newer-token", source="task",
                                  title="✅ 任务完成", message="new")
        # Old timer tries to fire with stale token
        Stopgate.fire_if_ours(path, "older-token", "old", "old-msg",
                              source="stop", delay=0, session_id="s1")
        self.assertEqual(self._fired, [])
        # State file preserved for the newer timer
        self.assertTrue(os.path.exists(path))

    def test_fire_if_ours_silent_when_file_missing(self):
        """PostToolBatch cancelled the gate — no notification."""
        Stopgate.fire_if_ours("/nonexistent/path", "tok", "T", "M",
                              source="stop", delay=0, session_id="s1")
        self.assertEqual(self._fired, [])

    def test_second_stop_in_same_turn_is_suppressed(self):
        """One user prompt → one notification. After the first fire marks
        the session notified, subsequent Stops in the same turn skip."""
        path1 = self._write_state("sess", "tok-1", source="stop")
        Stopgate.fire_if_ours(path1, "tok-1", "🎉 工作完成", "msg-1",
                              source="stop", delay=0, session_id="sess")
        self.assertEqual(len(self._fired), 1)
        # Second Stop arrives later in the same user turn
        path2 = self._write_state("sess", "tok-2", source="stop")
        Stopgate.fire_if_ours(path2, "tok-2", "🎉 工作完成", "msg-2",
                              source="stop", delay=0, session_id="sess")
        self.assertEqual(len(self._fired), 1)  # still only 1

    def test_user_prompt_submit_resets_notified_marker(self):
        """After UserPromptSubmit clears the marker, a new Stop can fire."""
        path1 = self._write_state("sess", "tok-1", source="stop")
        Stopgate.fire_if_ours(path1, "tok-1", "🎉", "msg-1",
                              source="stop", delay=0, session_id="sess")
        self.assertEqual(len(self._fired), 1)
        # New user prompt → reset
        Stopgate.reset_for_new_prompt("sess")
        # Now a new Stop can fire
        path2 = self._write_state("sess", "tok-2", source="stop")
        Stopgate.fire_if_ours(path2, "tok-2", "🎉", "msg-2",
                              source="stop", delay=0, session_id="sess")
        self.assertEqual(len(self._fired), 2)

    def test_fire_subtitle_differs_by_source(self):
        """TaskCompleted fires with subtitle='TaskCompleted',
        Stop with subtitle='Stop'."""
        # Stop path
        path = self._write_state("s1", "tok-stop", source="stop")
        Stopgate.fire_if_ours(path, "tok-stop", "🎉 工作完成", "msg",
                              source="stop", delay=0, session_id="s1")
        # TaskCompleted path
        path = self._write_state("s2", "tok-task", source="task")
        Stopgate.fire_if_ours(path, "tok-task", "✅ 任务完成", "msg",
                              source="task", delay=0, session_id="s2")
        self.assertEqual(self._fired[0][2], "Stop")
        self.assertEqual(self._fired[1][2], "TaskCompleted")

    def test_schedule_writes_state_file(self):
        """schedule() writes a valid state file that fire_if_ours can read.

        Uses a long delay so the forked subprocess doesn't actually
        fire during the test; we only verify the state file format.
        """
        try:
            Stopgate.schedule("s1-sched", "stop", "🎉 工作完成", "msg", delay=3600)
        except Exception:
            pass  # fork may fail in test env; state file is what matters
        path = self._state_file("s1-sched")
        self.assertTrue(os.path.exists(path), "schedule must write state file")
        with open(path) as f:
            state = json.load(f)
        self.assertEqual(state["source"], "stop")
        self.assertEqual(state["title"], "🎉 工作完成")
        self.assertEqual(state["message"], "msg")
        self.assertIn("token", state)
        # Cleanup: cancel so the long-delayed subprocess finds no file.
        Stopgate.cancel("s1-sched")

    def test_unsafe_session_id_sanitized(self):
        """Path separators in session_id must not escape state dir."""
        path = Stopgate.state_file_for("../escape/attempt")
        self.assertTrue(path.startswith(self._tmp + os.sep))
        self.assertNotIn("..", path)

    def test_fire_skips_when_transcript_grew(self):
        """Transcript growth means Claude resumed work — skip without
        setting notified marker so a later Stop can still fire."""
        transcript = os.path.join(self._tmp, "transcript.jsonl")
        with open(transcript, "w") as f:
            f.write('{"role":"assistant","content":"first"}\n')
        size_before = os.path.getsize(transcript)
        path = self._state_file("s1")
        with open(path, "w") as f:
            json.dump({"token": "tok", "source": "stop", "title": "T",
                        "message": "M", "ts": time.time(),
                        "transcript_path": transcript,
                        "transcript_size": size_before}, f)
        # Simulate Claude writing more to the transcript
        with open(transcript, "a") as f:
            f.write('{"role":"tool","content":"result"}\n')
        Stopgate.fire_if_ours(path, "tok", "T", "M", source="stop",
                              delay=0, session_id="s1",
                              transcript_path=transcript,
                              transcript_size=size_before)
        self.assertEqual(self._fired, [])
        self.assertFalse(Stopgate.already_notified("s1"))

    def test_fire_when_transcript_unchanged(self):
        """No transcript growth → Claude is done → fire notification."""
        transcript = os.path.join(self._tmp, "transcript.jsonl")
        with open(transcript, "w") as f:
            f.write('{"role":"assistant","content":"done"}\n')
        size = os.path.getsize(transcript)
        path = self._state_file("s1")
        with open(path, "w") as f:
            json.dump({"token": "tok", "source": "stop", "title": "T",
                        "message": "M", "ts": time.time(),
                        "transcript_path": transcript,
                        "transcript_size": size}, f)
        Stopgate.fire_if_ours(path, "tok", "T", "M", source="stop",
                              delay=0, session_id="s1",
                              transcript_path=transcript,
                              transcript_size=size)
        self.assertEqual(len(self._fired), 1)

    def test_fire_no_transcript_path_fires_normally(self):
        """reasonix has no transcript_path — timer alone decides."""
        path = self._write_state("s1", "tok")
        Stopgate.fire_if_ours(path, "tok", "T", "M", source="stop",
                              delay=0, session_id="s1")
        self.assertEqual(len(self._fired), 1)


if __name__ == "__main__":
    unittest.main()
