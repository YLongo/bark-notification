#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# Dependency (optional, only loaded when encryption is configured):
#   brew install cryptography  /  pip install cryptography
# Imported lazily inside _encrypt_aes_gcm so the script can run without it.

# Runtime configuration comes from a local config file with env
# overrides, so the repo can be public without leaking personal
# device keys:
#   ~/.config/bark-notification/config   KEY=VALUE lines — the single
#                                        source for all agents, GUI-
#                                        launched ones (ZCode) included.
#   BARK_BASE             Bark push URL incl. device key. Unset → Bark
#                         push is skipped; macOS notification still fires.
#   BARK_ENCRYPTION_KEY   Optional AES key (32 bytes) / IV (12 bytes).
#   BARK_ENCRYPTION_IV    Both must be valid to enable encryption; wrong
#                         length falls back to plaintext + warning.
# Environment variables override the file (CI / temporary switches).

_CONFIG_FILE = os.path.join(
    os.path.expanduser("~/.config/bark-notification"), "config"
)


class Config:
    """Resolved runtime configuration.

    `_load_config()` is the only reader of the config file and these
    environment variables; everything downstream receives the values
    by injection.
    """

    __slots__ = ("bark_url", "encryption_key", "encryption_iv")

    def __init__(self, bark_url, encryption_key, encryption_iv):
        self.bark_url = bark_url
        self.encryption_key = encryption_key
        self.encryption_iv = encryption_iv


def _read_config_file(path: str = None) -> dict:
    """Read KEY=VALUE lines from the config file.

    Missing file → {} (the normal case for new users). Blank lines and
    # comments are skipped; malformed lines are skipped with a warning
    that does NOT echo the line (it may contain a device key). Any
    read/decode failure keeps the values read so far and degrades
    loudly on stderr — hooks must never crash on config problems.
    """
    path = path or _CONFIG_FILE
    values = {}
    try:
        with open(path, "rb") as f:
            for lineno, raw in enumerate(f, 1):
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    print(
                        f"[bark-notify] {path}:{lineno}: ignoring "
                        f"undecodable line ({len(raw)} bytes)",
                        file=sys.stderr,
                    )
                    continue
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    print(
                        f"[bark-notify] {path}:{lineno}: ignoring malformed "
                        f"line ({len(line)} chars)",
                        file=sys.stderr,
                    )
                    continue
                key, _, value = line.partition("=")
                # Tolerate shell-export habits: BARK_BASE="…" or '…'
                # with stray whitespace — strip rather than silently
                # producing wrong-length keys.
                value = value.strip().strip('"').strip("'").strip()
                values[key.strip()] = value
    except FileNotFoundError:
        return values
    except OSError as e:
        print(
            f"[bark-notify] could not read config file {path}: "
            f"{type(e).__name__}",
            file=sys.stderr,
        )
    return values


def _load_config() -> Config:
    values = _read_config_file()
    bark_url = os.environ.get("BARK_BASE") or values.get("BARK_BASE") or None
    if not bark_url:
        print(
            "[bark-notify] BARK_BASE is not set; skipping Bark push "
            "(the macOS notification still fires). Set it in "
            "~/.config/bark-notification/config or export it, e.g. "
            "export BARK_BASE=https://api.day.app/YOUR_KEY",
            file=sys.stderr,
        )
    return Config(
        bark_url=bark_url,
        encryption_key=(
            os.environ.get("BARK_ENCRYPTION_KEY", "")
            or values.get("BARK_ENCRYPTION_KEY", "")
        ),
        encryption_iv=(
            os.environ.get("BARK_ENCRYPTION_IV", "")
            or values.get("BARK_ENCRYPTION_IV", "")
        ),
    )

OPENAI_ICON_URL = "https://images.ctfassets.net/j22is2dtoxu1/intercom-img-d177d076c9a5453052925143/49d5d812b0a6fcc20a14faa8c629d9fb/icon-ios-1024_401x.png"
# Claude symbol (CC0) from Wikimedia, publicly accessible without auth.
CLAUDE_ICON_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Claude_AI_symbol.svg/960px-Claude_AI_symbol.svg.png"
# OpenCode icon
OPENCODE_ICON_URL = "https://opencode.ai/apple-touch-icon.png"
# Reasonix logo (PNG from reasonix-desktop repo; Bark iOS doesn't render SVG)
REASONIX_ICON_URL = "https://raw.githubusercontent.com/suply/reasonix-desktop/master/build/icon.png"
# Pi coding agent — PNG Homarr dashboard icon (Bark iOS doesn't support SVG)
PI_ICON_URL = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/pi-coding-agent.png"
# ZCode (Z.ai) — official desktop app icon PNG from the open-source repo
ZCODE_ICON_URL = "https://raw.githubusercontent.com/zai-org/ZCode/main/packages/desktop/build/icons/256x256.png"

def _encrypt_aes_gcm(plaintext: bytes, key: bytes, iv: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)
    encrypted = aesgcm.encrypt(iv, plaintext, None)
    return base64.b64encode(encrypted).decode("ascii")


class PlainForm:
    """Bark POST body: urlencode the push payload as-is."""

    def __init__(self, payload: dict):
        self._payload = payload

    def body(self) -> str:
        return urllib.parse.urlencode(self._payload)


class EncryptedForm:
    """Bark POST body: encrypt the push payload as JSON.

    Body fields: ciphertext (base64) and iv (the original text IV).

    `encrypt_fn` defaults to the AES-256-GCM helper; tests can inject a
    deterministic stand-in (e.g. XOR) to exercise the wire format
    without requiring the optional `cryptography` dependency.
    """

    def __init__(
        self,
        payload: dict,
        key: bytes,
        iv_bytes: bytes,
        iv_text: str,
        encrypt_fn=None,
    ):
        self._payload = payload
        self._key = key
        self._iv = iv_bytes
        self._iv_text = iv_text
        self._encrypt_fn = encrypt_fn or _encrypt_aes_gcm

    def body(self) -> str:
        plaintext = json.dumps(
            self._payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ciphertext = self._encrypt_fn(plaintext, self._key, self._iv)
        return urllib.parse.urlencode(
            {"ciphertext": ciphertext, "iv": self._iv_text}
        )


class BarkCipher:
    """Strategy: pick PlainForm or EncryptedForm based on config."""

    _KEY_LEN = 32
    _IV_LEN = 12

    @classmethod
    def from_config(cls, key: str, iv: str, payload: dict):
        # Encryption not configured: silent opt-out, no warning.
        if not key and not iv:
            return PlainForm(payload)
        # Configured but wrong shape: fall back to plaintext, but warn —
        # silent fallback here is the same "no notification, no error"
        # trap as the missing-`cryptography` case guarded below.
        if (
            len(key.encode("utf-8")) != cls._KEY_LEN
            or len(iv.encode("utf-8")) != cls._IV_LEN
        ):
            print(
                "[bark-notify] BARK_ENCRYPTION_KEY must be 32 chars and "
                "BARK_ENCRYPTION_IV 12 chars; sending as plaintext.",
                file=sys.stderr,
            )
            return PlainForm(payload)
        # Encryption is configured; verify the optional dependency is
        # actually available. If not, fall back to plaintext but warn
        # loudly — silent fallback was the root cause of "no notification,
        # no error" confusion when users configured encryption but hadn't
        # installed `cryptography`.
        if not cls._crypto_available():
            print(
                "[bark-notify] ENCRYPTION_KEY / ENCRYPTION_IV are set but "
                "the 'cryptography' package is not installed; sending as "
                "plaintext. Install with: pip install cryptography",
                file=sys.stderr,
            )
            return PlainForm(payload)
        return EncryptedForm(
            payload,
            key=key.encode("utf-8"),
            iv_bytes=iv.encode("utf-8"),
            iv_text=iv,
        )

    @staticmethod
    def _crypto_available() -> bool:
        try:
            import cryptography  # noqa: F401
            return True
        except ImportError:
            return False


def _load_payload() -> dict:
    if len(sys.argv) > 1:
        try:
            return _coerce_payload_dict(json.loads(sys.argv[1]))
        except json.JSONDecodeError:
            return {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read().strip()
            if raw:
                return _coerce_payload_dict(json.loads(raw))
    except Exception:
        return {}
    return {}


def _coerce_payload_dict(parsed) -> dict:
    """Reject non-dict JSON so callers can't accidentally pass an int/list
    and trigger AttributeError downstream (which would be swallowed)."""
    if isinstance(parsed, dict):
        return parsed
    print(
        f"[bark-notify] expected a JSON object, got {type(parsed).__name__}; "
        "ignoring payload",
        file=sys.stderr,
    )
    return {}


class SourceDetector:
    """Resolve the AgentSource of a payload.

    Order of resolution:
      1. Explicit `source` tag from the caller (preferred).
      2. Heuristic sniff, preserved verbatim from the original ladder so
         existing callers that don't tag their payload keep working.

    Returns one of: "claude", "opencode", "reasonix", "pi", "zcode", "codex".
    """

    _KNOWN = ("claude", "opencode", "reasonix", "pi", "zcode", "codex")

    @classmethod
    def detect(cls, payload: dict) -> str:
        # Explicit env var from the hook config wins — reasonix's
        # settings.json sets BARK_AGENT_SOURCE=reasonix per-hook, which
        # is 100% reliable vs guessing from payload shape.
        env_tag = os.environ.get("BARK_AGENT_SOURCE")
        if env_tag in cls._KNOWN:
            return env_tag
        tag = payload.get("source")
        if tag in cls._KNOWN:
            return tag
        return cls._heuristic(payload)

    @staticmethod
    def _heuristic(payload: dict) -> str:
        # ZCode (Z.ai) checks come FIRST because its hook payload is
        # Claude-compatible (it carries hook_event_name / session_id /
        # transcript_path) and would otherwise be misidentified as
        # Claude Code. Reliable ZCode-only markers, confirmed against
        # the open-source client (configured-runner-input.ts):
        #   - ZCODE_SESSION_ID / ZCODE_PROJECT_DIR env vars, injected
        #     into every hook subprocess.
        #   - camelCase duplicates (hookEventName / transcriptPath) that
        #     Claude Code never sends.
        #   - `agent_type` top-level field.
        #   - transcript files under a "zcode-hook-*" temp dir.
        if os.environ.get("ZCODE_SESSION_ID") or os.environ.get(
            "ZCODE_PROJECT_DIR"
        ):
            return "zcode"
        if payload.get("hookEventName") or payload.get("transcriptPath"):
            return "zcode"
        if payload.get("agent_type"):
            return "zcode"
        if "zcode-hook" in (payload.get("transcript_path") or ""):
            return "zcode"
        if payload.get("hook_event_name"):
            return "claude"
        if payload.get("session_id") or payload.get("transcript_path"):
            return "claude"
        # Reasonix native payload uses camelCase keys
        if payload.get("sessionId") or payload.get("lastAssistantText"):
            return "reasonix"
        title = payload.get("title") or ""
        if "Claude" in title:
            return "claude"
        if "OpenCode" in title or "opencode" in title.lower():
            return "opencode"
        if "Reasonix" in title or "reasonix" in title.lower():
            return "reasonix"
        if "ZCode" in title or "zcode" in title.lower():
            return "zcode"
        if "Pi" in title or "pi" in title.lower():
            return "pi"
        event_type = payload.get("event") or payload.get("type") or ""
        if event_type.startswith("session.") or event_type in ("session_completed", "file_edited"):
            return "opencode"
        return "codex"


class PushBuilder:
    """Build a Bark PushPayload from a raw payload and resolved AgentSource.

    Pure function. Knows nothing about transport or encryption.
    """

    _TITLE_BY_SOURCE = {
        "claude": "Claude Code",
        "opencode": "OpenCode",
        "reasonix": "Reasonix",
        "pi": "Pi",
        "zcode": "ZCode",
        "codex": "Codex",
    }
    _ICON_BY_SOURCE = {
        "claude": CLAUDE_ICON_URL,
        "opencode": OPENCODE_ICON_URL,
        "reasonix": REASONIX_ICON_URL,
        "pi": PI_ICON_URL,
        "zcode": ZCODE_ICON_URL,
        "codex": OPENAI_ICON_URL,
    }

    @classmethod
    def build(cls, payload: dict, source: str) -> dict:
        event_type = (
            payload.get("hook_event_name")
            or payload.get("type")
            or payload.get("event")
        )
        title = payload.get("title") or cls._TITLE_BY_SOURCE.get(source, "Codex")
        icon_url = cls._ICON_BY_SOURCE.get(source, OPENAI_ICON_URL)
        message = cls._truncate(cls._resolve_message(payload, event_type))

        out = {
            "title": title,
            "markdown": message,
            "icon": icon_url,
            "action": "none",
        }
        if event_type:
            out["subtitle"] = event_type
        return out

    @classmethod
    def title_for(cls, source: str) -> str:
        """Display title for an AgentSource ("claude" → "Claude Code")."""
        return cls._TITLE_BY_SOURCE.get(source, "Codex")

    @classmethod
    def from_parts(cls, agent_source: str, title: str, message: str, subtitle: str = None) -> dict:
        """Assemble a PushPayload from precomputed display fields.

        The dual of build(): for callers (the Stopgate timer subprocess)
        that no longer hold the raw payload, only the display fields
        captured at schedule time. Icon and action knowledge lives in
        PushBuilder — here and in build(), nowhere else.
        """
        out = {
            "title": title,
            "markdown": cls._truncate(message),
            "icon": cls._ICON_BY_SOURCE.get(agent_source, OPENAI_ICON_URL),
            "action": "none",
        }
        if subtitle:
            out["subtitle"] = subtitle
        return out

    _MAX_MESSAGE = 500

    @classmethod
    def _truncate(cls, message: str) -> str:
        """Single choke point for the display-length cap on markdown."""
        if len(message) > cls._MAX_MESSAGE:
            return message[: cls._MAX_MESSAGE - 3] + "..."
        return message

    @staticmethod
    def _resolve_message(payload: dict, event_type):
        for key in ("last-assistant-message", "last_assistant_message",
                     "lastAssistantText", "message", "summary"):
            v = payload.get(key)
            if v:
                return v
        # Tool events (ZCode PermissionRequest, Claude Code
        # PreToolUse/Notification, ...): name the tool so the push says
        # more than just the event name.
        tool_name = payload.get("tool_name") or payload.get("toolName")
        cwd = payload.get("cwd")
        if tool_name and event_type:
            return f"{event_type}: {tool_name}" + (f" in {cwd}" if cwd else "")
        if cwd and event_type:
            return f"{event_type} in {cwd}"
        if cwd:
            return f"Event in {cwd}"
        if event_type:
            return f"Event: {event_type}"
        return "Event"


class BarkChannel:
    """Side-effect boundary: POST a form-encoded body to Bark.

    `url=None` (BARK push unconfigured) makes send() a logged no-op.
    `opener` defaults to urllib.request.urlopen; tests inject a fake to
    avoid real network calls.
    """

    def __init__(self, url, opener=None):
        self._url = url
        self._opener = opener or urllib.request.urlopen

    def send(self, form_body: str) -> None:
        if not self._url:
            _log_notify("BarkChannel.send skipped: BARK_BASE not configured")
            return
        req = urllib.request.Request(
            self._url,
            data=form_body.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=5) as response:
                response.read()
            _log_notify(f"BarkChannel.send OK: {len(form_body)} bytes")
        except Exception as e:
            # Do not block Codex runs on notification failures, but make
            # the failure observable — silent swallow hid every real
            # network/SSL/DNS bug during initial setup.
            _log_notify(f"BarkChannel.send FAILED: {type(e).__name__}: {e}")
            print(
                f"[bark-notify] send failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return




class MacOSNotifier:
    """Side-effect boundary: surface a notification via macOS Notification Center.

    `runner` defaults to subprocess.run; tests inject a fake to avoid
    real osascript/afplay invocations.
    """

    _SOUND_PATH = "/System/Library/Sounds/Glass.aiff"

    def __init__(self, runner=None):
        self._runner = runner or subprocess.run

    def notify(self, title: str, subtitle: str | None, message: str) -> None:
        script = f'display notification "{message}" with title "{title}"'
        if subtitle:
            script += f' subtitle "{subtitle}"'
        try:
            self._runner(
                ["osascript", "-e", script],
                capture_output=True,
            )
            subprocess.Popen(
                ["afplay", self._SOUND_PATH],
                start_new_session=True,
            )
        except Exception as e:
            print(
                f"[bark-notify] macos notify failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return


def _has_running_background_tasks(payload: dict) -> bool:
    """Stop hook: are there still in-flight background tasks?

    Claude Code v2.1.145+ populates `background_tasks` on Stop payloads
    with a list of {status: "running"|...} entries. If any task is still
    running, the agent is not really done — suppress the "completed"
    notification so the user is not told the task is finished when it
    isn't. Missing/empty list or unknown shapes return False.
    """
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return False
    return any(
        isinstance(t, dict) and t.get("status") == "running" for t in tasks
    )


class Stopgate:
    """5-second debounce state machine shared by Stop and TaskCompleted.

    Problem this solves:
      - `Stop` fires every time Claude finishes a turn, even mid-task
        when it's about to continue. We only want to notify on the
        FINAL stop.
      - `TaskCompleted` fires once per task. Multi-task turns would
        spam notifications; we want one notification when the last
        task finishes.

    Mechanism:
      - On Stop/TaskCompleted, write a state file tagged with the
        source ("stop" or "task") and spawn a 5-second timer process.
      - On PostToolBatch (Claude resumed work) the state file is
        deleted — the timer process will see it's gone and do nothing.
      - A newer Stop/TaskCompleted overwrites the state file with a
        new token; the older timer process sees a token mismatch and
        does nothing.
      - When the timer fires, if "our" state file is still current,
        send the notification.

    Why a forked subprocess, not a thread:
      Hook scripts are one-shot processes. The main python process
      exits as soon as main() returns, killing any daemon threads
      before the 5-second timer can fire. We fork a detached
      subprocess that outlives the parent so the timer can actually
      complete. State is passed via env vars + the state file.

    State file location: temp dir, keyed by session_id so concurrent
    sessions don't clobber each other.
    """

    _GATE_SECONDS = 10.0
    _STATE_DIR = os.path.join(tempfile.gettempdir(), "bark-stopgate")

    @classmethod
    def state_file_for(cls, session_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id or "default")
        try:
            os.makedirs(cls._STATE_DIR, exist_ok=True)
        except OSError:
            pass
        return os.path.join(cls._STATE_DIR, f"state_{safe}.json")

    @classmethod
    def notified_marker_for(cls, session_id: str) -> str:
        """Per-session 'already notified' marker file.

        Written after a notification fires; deleted on UserPromptSubmit.
        Prevents multiple Stops within one user-initiated turn from
        each sending a notification — the first Stop's notification
        sets this marker, and subsequent Stops see it and skip.
        """
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id or "default")
        return os.path.join(cls._STATE_DIR, f"notified_{safe}.json")

    @classmethod
    def already_notified(cls, session_id: str) -> bool:
        return os.path.exists(cls.notified_marker_for(session_id))

    @classmethod
    def mark_notified(cls, session_id: str) -> None:
        try:
            with open(cls.notified_marker_for(session_id), "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass

    @classmethod
    def reset_for_new_prompt(cls, session_id: str) -> None:
        """UserPromptSubmit: clear the notified marker so a new user
        turn can fire a fresh notification."""
        for sid in (session_id, "default"):
            try:
                os.unlink(cls.notified_marker_for(sid))
            except (FileNotFoundError, OSError):
                pass

    @classmethod
    def cancel(cls, session_id: str) -> None:
        """PostToolBatch / new event: Claude resumed work, cancel pending."""
        try:
            os.unlink(cls.state_file_for(session_id))
        except (FileNotFoundError, OSError):
            pass

    @classmethod
    def schedule(
        cls,
        session_id: str,
        source: str,
        title: str,
        message: str,
        delay: float = None,
        agent_source: str = "codex",
        transcript_path: str = None,
    ) -> None:
        """Write state file + fork a detached timer subprocess.

        When the timer fires, if our state file is still current,
        the subprocess re-enters via fire_if_ours and sends the
        notification. `source` is the trigger ("stop" or "task").
        `agent_source` is who triggered it ("claude", "reasonix",
        "opencode", "codex") — selects the icon.

        `transcript_path` (Claude Code only): if provided, the timer
        will check whether the transcript file grew since schedule
        time. If it grew, Claude resumed work → skip notification.

        `delay` overrides _GATE_SECONDS (mainly for tests).
        """
        delay = delay if delay is not None else cls._GATE_SECONDS
        token = f"{time.time()}-{os.getpid()}"
        # NOTE: no display truncation here — the message flows into the
        # state file untrimmed and is capped once, at emit time, by
        # PushBuilder.from_parts (single choke point).
        transcript_size = cls._transcript_size(transcript_path)
        state = {
            "token": token,
            "source": source,
            "title": title,
            "message": message,
            "ts": time.time(),
            "transcript_path": transcript_path or "",
            "transcript_size": transcript_size,
        }
        path = cls.state_file_for(session_id)
        try:
            with open(path, "w") as f:
                json.dump(state, f)
        except OSError:
            return

        # Fork a detached subprocess that outlives this hook process.
        # It re-enters the script with _BARK_STOPGATE_FIRE env set;
        # see fire_if_ours() for the child-side handler.
        env = dict(os.environ)
        env["_BARK_STOPGATE_FIRE"] = "1"
        env["_BARK_STOPGATE_PATH"] = path
        env["_BARK_STOPGATE_TOKEN"] = token
        env["_BARK_STOPGATE_TITLE"] = title
        env["_BARK_STOPGATE_MESSAGE"] = message
        env["_BARK_STOPGATE_SOURCE"] = source
        env["_BARK_STOPGATE_AGENT_SOURCE"] = agent_source
        env["_BARK_STOPGATE_SESSION_ID"] = session_id
        env["_BARK_STOPGATE_DELAY"] = str(delay)
        env["_BARK_STOPGATE_TRANSCRIPT_PATH"] = transcript_path or ""
        env["_BARK_STOPGATE_TRANSCRIPT_SIZE"] = str(transcript_size)
        try:
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            print(
                f"[bark-notify] stopgate spawn failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    @staticmethod
    def _transcript_size(path: str) -> int:
        """Return current file size of the transcript, or 0 if unavailable."""
        if not path:
            return 0
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @classmethod
    def fire_if_ours(
        cls,
        path: str,
        token: str,
        title: str,
        message: str,
        source: str,
        delay: float,
        session_id: str = "default",
        agent_source: str = "codex",
        transcript_path: str = "",
        transcript_size: int = 0,
    ) -> int:
        """Child-subprocess entry point. Sleep, then check state file.

        Returns exit code: 0 if fired, 0 if cancelled (silent).

        Guards (checked in order):
          1. Token mismatch — a newer event overwrote the state file.
          2. Transcript growth — Claude Code resumed writing to its
             transcript, meaning it's still working. Skip WITHOUT
             setting the notified marker so a later Stop can still fire.
          3. Already-notified marker — a previous Stop already notified
             for this user turn. Cleared on UserPromptSubmit.
        """
        time.sleep(delay)
        try:
            with open(path, "r") as f:
                cur = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return 0
        if cur.get("token") != token:
            _log_notify(f"fire_if_ours: SKIP token mismatch")
            return 0
        # Transcript growth check (Claude Code only). If the transcript
        # file grew since schedule time, Claude resumed work — skip
        # this notification without marking notified, so the next Stop
        # can fire a notification when Claude truly finishes.
        tp = transcript_path or cur.get("transcript_path", "")
        ts_old = transcript_size or cur.get("transcript_size", 0)
        if tp and ts_old:
            ts_now = cls._transcript_size(tp)
            if ts_now > ts_old:
                _log_notify(
                    f"fire_if_ours: SKIP transcript grew "
                    f"{ts_old}→{ts_now} ({tp})"
                )
                try:
                    os.unlink(path)
                except OSError:
                    pass
                return 0
        if cls.already_notified(session_id):
            _log_notify(f"fire_if_ours: SKIP already notified")
            try:
                os.unlink(path)
            except OSError:
                pass
            return 0
        try:
            os.unlink(path)
        except OSError:
            pass
        cls.mark_notified(session_id)
        subtitle = "TaskCompleted" if source == "task" else "Stop"
        _log_notify(f"fire_if_ours: FIRING source={source} agent={agent_source}")
        _send_notification(title, message, subtitle=subtitle, agent_source=agent_source)
        return 0


def _debug_log_stop(payload: dict, suppressed: bool) -> None:
    """Append a one-line Stop trace to /tmp/bark_stop_debug.log."""
    if payload.get("hook_event_name") != "Stop":
        return
    tasks = payload.get("background_tasks")
    n_tasks = len(tasks) if isinstance(tasks, list) else 0
    n_running = (
        sum(
            1
            for t in tasks
            if isinstance(t, dict) and t.get("status") == "running"
        )
        if isinstance(tasks, list)
        else 0
    )
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"stop_hook_active={payload.get('stop_hook_active')} "
        f"bg_tasks={n_tasks} bg_running={n_running} "
        f"suppressed={suppressed}\n"
    )
    try:
        with open("/tmp/bark_stop_debug.log", "a") as f:
            f.write(line)
    except OSError:
        pass


def _log_notify(msg: str) -> None:
    """Append to /tmp/bark_notify_debug.log. stderr is DEVNULL in the
    Stopgate subprocess, so file logging is the only way to see errors
    from Bark send failures."""
    try:
        with open("/tmp/bark_notify_debug.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _emit_push(push: dict, channel=None, notifier=None) -> None:
    """Single emit seam: deliver one PushPayload over both channels.

    Absorbs the whole transport assembly — config load, cipher, form
    encoding, the Bark POST, the macOS notification — so no caller
    assembles this chain by hand. `channel` / `notifier` are injectable
    for tests (same style as BarkCipher's encrypt_fn and BarkChannel's
    opener).
    """
    config = _load_config()
    cipher = BarkCipher.from_config(
        key=config.encryption_key, iv=config.encryption_iv, payload=push
    )
    try:
        form = cipher.body()
    except Exception as e:
        _log_notify(f"cipher.body() FAILED: {type(e).__name__}: {e}")
        return
    (channel or BarkChannel(url=config.bark_url)).send(form)
    (notifier or MacOSNotifier()).notify(
        push.get("title", ""), push.get("subtitle"), push.get("markdown", "")
    )


def _send_notification(title: str, message: str, subtitle: str = None, agent_source: str = "codex") -> None:
    """Fire Bark + macOS notification. Called by the Stopgate timer.

    Thin assembly: turn the display fields captured at schedule time
    into a PushPayload, then hand it to the emit seam. Tests stub this
    function to observe Stopgate fires — keep the signature stable.
    """
    _log_notify(f"send_notification: agent={agent_source} title={title!r} msg_len={len(message)}")
    _emit_push(PushBuilder.from_parts(agent_source, title, message, subtitle))


def main() -> None:
    # Subprocess mode: Stopgate forked us to fire after the gate delay.
    # Handle and exit before any normal hook processing.
    if os.environ.get("_BARK_STOPGATE_FIRE") == "1":
        sys.exit(
            Stopgate.fire_if_ours(
                path=os.environ["_BARK_STOPGATE_PATH"],
                token=os.environ["_BARK_STOPGATE_TOKEN"],
                title=os.environ["_BARK_STOPGATE_TITLE"],
                message=os.environ["_BARK_STOPGATE_MESSAGE"],
                source=os.environ["_BARK_STOPGATE_SOURCE"],
                delay=float(os.environ["_BARK_STOPGATE_DELAY"]),
                session_id=os.environ.get("_BARK_STOPGATE_SESSION_ID", "default"),
                agent_source=os.environ.get("_BARK_STOPGATE_AGENT_SOURCE", "codex"),
                transcript_path=os.environ.get("_BARK_STOPGATE_TRANSCRIPT_PATH", ""),
                transcript_size=int(os.environ.get("_BARK_STOPGATE_TRANSCRIPT_SIZE", "0")),
            )
        )

    payload = _load_payload()
    # Detect the agent source from the ORIGINAL payload shape before
    # field normalization — reasonix's camelCase keys (sessionId,
    # lastAssistantText) identify it, but they'd be masked once we
    # copy them into Claude Code's snake_case equivalents below.
    agent_source = SourceDetector.detect(payload)
    agent_title = PushBuilder.title_for(agent_source)
    # Field normalization: reasonix's native hook payload uses camelCase
    # keys (event/sessionId/lastAssistantText) while Claude Code uses
    # snake_case (hook_event_name/session_id/last_assistant_message).
    # Normalize to the snake_case shape the rest of main() expects so a
    # single code path handles both callers.
    if "hook_event_name" not in payload and payload.get("event"):
        payload["hook_event_name"] = payload["event"]
    if "session_id" not in payload and payload.get("sessionId"):
        payload["session_id"] = payload["sessionId"]
    if "last_assistant_message" not in payload and payload.get("lastAssistantText"):
        payload["last_assistant_message"] = payload["lastAssistantText"]

    event = payload.get("hook_event_name") or payload.get("type") or payload.get("event")
    session_id = payload.get("session_id") or "default"

    # UserPromptSubmit: new user turn starting. Reset the "already
    # notified" marker so this turn can fire a fresh notification.
    if event == "UserPromptSubmit":
        Stopgate.reset_for_new_prompt(session_id)
        return

    # PostToolBatch: Claude resumed work mid-turn. Cancel any pending
    # Stop/TaskCompleted notification from the previous pause.
    if event == "PostToolBatch":
        Stopgate.cancel(session_id)
        return

    # TaskCompleted: Claude explicitly marked a task done. Schedule
    # notification via the gate so multiple tasks in one turn collapse
    # into a single notification when the last one completes.
    if event == "TaskCompleted":
        task_subject = payload.get("task_subject") or "Task complete"
        Stopgate.schedule(
            session_id=session_id,
            source="task",
            title=f"✅ {agent_title} 任务完成",
            message=task_subject,
            agent_source=agent_source,
            transcript_path=payload.get("transcript_path"),
        )
        return

    # Stop: Claude finished a turn. Skip background-task suppression
    # now handled by PostToolBatch cancelling the gate. Schedule via
    # the gate so a Stop followed by Claude continuing doesn't notify.
    if event == "Stop":
        if _has_running_background_tasks(payload):
            _debug_log_stop(payload, suppressed=True)
            return
        _debug_log_stop(payload, suppressed=False)
        last_msg = payload.get("last_assistant_message") or "工作完成"
        Stopgate.schedule(
            session_id=session_id,
            source="stop",
            title=f"🎉 {agent_title} 工作完成",
            message=last_msg,
            agent_source=agent_source,
            transcript_path=payload.get("transcript_path"),
        )
        return

    # Notification (permission/idle) + other events: fire immediately.
    push_payload = PushBuilder.build(payload, agent_source)
    _log_notify(
        f"direct_notify: agent={agent_source} event={event} "
        f"title={push_payload.get('title')!r} "
        f"msg_len={len(push_payload.get('markdown', ''))}"
    )
    _emit_push(push_payload)


if __name__ == "__main__":
    main()
