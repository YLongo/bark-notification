/**
 * Bark Notify for Pi
 *
 * Sends push notifications to iOS via Bark when Pi finishes work.
 *
 * Notification carries details:
 *   - title:    🍕 Pi 完成 · {会话名 || 项目名}
 *   - subtitle: {项目名} · 耗时 {4m12s}(无计时信息时省略)
 *   - body:     最后一条 assistant 回复(压平空白,截断 400 字)
 *
 * Setup:
 *   1. Install Bark app on iOS
 *   2. Edit BARK_SCRIPT path below
 *   3. Place at ~/.pi/agent/extensions/bark-notify-pi.ts
 *   4. /reload or restart pi
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// ⬇️ 必改：指向你本地的 bark_notification.py
const BARK_SCRIPT = "/path/to/bark_notification.py";

/** Body 截断上限：比脚本侧 PushBuilder 的 500 字更紧，通知列表里更紧凑 */
const MAX_BODY_CHARS = 400;
/** 标题里会话名截断上限 */
const MAX_TITLE_CHARS = 24;

export default function (pi: ExtensionAPI) {
  let startedAt: number | undefined;

  pi.on("agent_start", async () => {
    startedAt = Date.now();
  });

  pi.on("agent_settled", async (_event, ctx) => {
    const started = startedAt;
    startedAt = undefined;
    // Small delay so TUI settles, then fire immediately.
    setTimeout(() => {
      sendBarkNotify(buildPayload(ctx, started));
    }, 1500);
  });
}

function buildPayload(ctx: any, started: number | undefined): Record<string, string> {
  const project = shortProject(ctx?.cwd as string | undefined);
  const sessionName = truncate(
    String(ctx?.sessionManager?.getSessionName?.() ?? ""),
    MAX_TITLE_CHARS
  );
  const duration = started !== undefined ? formatDuration(Date.now() - started) : "";

  const title = `🍕 Pi 完成 · ${sessionName || project}`;
  const subtitle = [project, duration && `耗时 ${duration}`]
    .filter(Boolean)
    .join(" · ");

  return {
    source: "pi",
    title,
    // bark_notification.py 把 payload.type 映射为 Bark subtitle
    type: subtitle,
    last_assistant_message: lastAssistantText(ctx) || "(无文本回复)",
  };
}

/** 从 entries 尾部找最后一条 assistant 消息的纯文本 */
function lastAssistantText(ctx: any): string {
  const entries = ctx?.sessionManager?.getEntries?.();
  if (!Array.isArray(entries)) return "";
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e?.type !== "message" || e.message?.role !== "assistant") continue;
    const content = e.message?.content;
    if (!Array.isArray(content)) continue;
    const text = content
      .filter((b: any) => b?.type === "text" && typeof b.text === "string")
      .map((b: any) => b.text)
      .join("\n")
      .trim();
    if (text) return flatten(text);
  }
  return "";
}

/** 压平空白:连续空白/换行折叠成单空格,通知列表里更紧凑 */
function flatten(s: string): string {
  return truncate(s.replace(/\s+/g, " ").trim(), MAX_BODY_CHARS);
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

/** ~/project/my-app → my-app;解析失败回退整串 */
function shortProject(cwd: string | undefined): string {
  if (!cwd) return "unknown";
  const parts = cwd.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || cwd;
}

/** 58s / 4m12s / 1h02m */
function formatDuration(ms: number): string {
  const total = Math.max(1, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${total}s`;
}

function sendBarkNotify(payload: Record<string, string>) {
  const body = JSON.stringify(payload);
  try {
    const cp = require("child_process");
    const proc = cp.spawn("python3", [BARK_SCRIPT], {
      env: { ...process.env },
      stdio: ["pipe", "ignore", "ignore"],
      detached: true,
    });
    proc.stdin!.end(body);
    proc.unref();
  } catch (_e) {
    // ignore
  }
}
