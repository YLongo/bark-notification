# 适用于 [Claude Code](https://code.claude.com/docs/en/overview)/[Codex CLI](https://github.com/openai/codex)/[OpenCode](https://opencode.ai)/[Pi](https://pi.dev)/[ZCode](https://zcode.z.ai)/[Reasonix](https://github.com/suply/reasonix-desktop) 的 Bark 通知

当你的 AI 编程 agent 完成任务时，通过 [Bark](https://github.com/Finb/Bark) 向 iOS 设备推送通知，同时在 Mac 本地弹出系统通知。

## 特性

- **统一脚本**：一个脚本支持 Claude Code、Codex CLI、OpenCode、Pi、ZCode、Reasonix
- **自动识别**：根据 payload 结构识别通知来源，自动配上对应的图标和标题
- **双通道**：Bark iOS 推送 + macOS 本地通知
- **可选端到端加密**：AES-256-GCM，Apple 和 Bark 服务器都无法读取通知内容

## 前置条件

- Python 3.10+——明文推送零第三方依赖，标准库即可
- iOS 设备上安装 [Bark](https://apps.apple.com/app/id1403753865) App
- `cryptography` 库仅启用加密时需要：`pip install cryptography`（macOS 也可 `brew install cryptography`）

未安装 `cryptography` 时脚本正常运行（明文推送）；配置了加密但缺库，脚本会警告并回退明文，不会静默失败。

## 安装配置

### 1. 获取推送 URL

打开 Bark App，首页即可看到你的推送 URL，形如 `https://api.day.app/YOUR_DEVICE_KEY`——复制留用，下一步要用。

### 2. 配置脚本

把配置写进本地配置文件——一次配置，所有 agent 生效（含 GUI 启动的 ZCode）：

```bash
mkdir -p ~/.config/bark-notification
echo 'BARK_BASE=https://api.day.app/YOUR_DEVICE_KEY' \
  >> ~/.config/bark-notification/config
```

文件为 `KEY=VALUE` 纯文本，即时生效：值不要加引号，`#` 注释须在行首。文件里存着设备密钥，建议 `chmod 600` 保护。三个可用变量：

- `BARK_BASE` —— 推送必需项。未设置时脚本跳过 Bark 推送（stderr 给出提示），macOS 本地通知照常弹出。
- `BARK_ENCRYPTION_KEY` / `BARK_ENCRYPTION_IV` —— 可选，见「可选：启用端到端加密」。

环境变量可临时覆盖文件（CI / 调试 / 单会话换设备）：

```bash
export BARK_BASE="https://api.day.app/YOUR_DEVICE_KEY"
```

`BARK_AGENT_SOURCE`（可选，仅环境变量）强制指定通知来源（`claude`/`opencode`/`reasonix`/`pi`/`zcode`/`codex`），优先级高于 payload 识别——适合在 hook 配置里按会话设置。

### 3. 配置你的编程 agent

#### Claude Code

编辑 `~/.claude/settings.json`：

```json
{
  "hooks": {
    "Notification": [
      {
        "hooks": [
          {
            "command": "python3 /path/to/bark_notification.py",
            "type": "command"
          }
        ],
        "matcher": ""
      }
    ]
  }
}
```

#### Codex CLI

编辑 `~/.codex/config.toml`：

```toml
notify = ["python3", "/path/to/bark_notification.py"]
```

#### ZCode

ZCode 的 hook 协议与 Claude 兼容，无需额外适配——脚本会自动识别 ZCode（ZCode 会向每个 hook 进程注入 `ZCODE_SESSION_ID`/`ZCODE_PROJECT_DIR` 环境变量，且 payload 中携带 `hookEventName`/`transcriptPath` 等 camelCase 重复字段）。

编辑 `~/.zcode/cli/config.json`（**仅用户级**——出于安全考虑 ZCode 忽略项目级 hook 配置）：

```json
{
  "hooks": {
    "enabled": true,
    "events": {
      "UserPromptSubmit": [
        {
          "hooks": [
            {
              "type": "process",
              "command": "python3",
              "args": ["/path/to/bark_notification.py"],
              "timeoutMs": 15000
            }
          ]
        }
      ],
      "Stop": [
        {
          "hooks": [
            {
              "type": "process",
              "command": "python3",
              "args": ["/path/to/bark_notification.py"],
              "timeoutMs": 15000
            }
          ]
        }
      ],
      "PermissionRequest": [
        {
          "hooks": [
            {
              "type": "process",
              "command": "python3",
              "args": ["/path/to/bark_notification.py"],
              "timeoutMs": 15000
            }
          ]
        }
      ]
    }
  }
}
```

说明：

- `UserPromptSubmit` 必需——它重置每回合的通知状态，保证新回合还能收到通知。
- `Stop` 在模型结束时触发；通知有 10 秒防抖，正文显示最后一条 assistant 消息。
- `PermissionRequest`（可选）在 ZCode 需要你批准权限时推送——手机 SSH 场景很好用。
- hook 配置在会话启动时快照；改完后**请开启新会话**。若 hook 不触发，查 `~/.zcode/cli/log/zcode-<date>.jsonl` 中的 `hook.run.failed`。

配置文件对所有启动方式生效——GUI（Dock/Spotlight）启动的 ZCode 无需任何特殊处理。

#### Reasonix

Reasonix 的 hook 事件与 Claude 同族，且支持在 hook 里注入环境变量——用 `BARK_AGENT_SOURCE` 打上来源标记即可精确识别。

编辑 `~/.reasonix/settings.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "command": "python3 /path/to/bark_notification.py",
        "timeout": 10000,
        "env": { "BARK_AGENT_SOURCE": "reasonix" }
      }
    ],
    "Notification": [
      {
        "command": "python3 /path/to/bark_notification.py",
        "timeout": 10000,
        "env": { "BARK_AGENT_SOURCE": "reasonix" }
      }
    ],
    "UserPromptSubmit": [
      {
        "command": "python3 /path/to/bark_notification.py",
        "timeout": 5000,
        "env": { "BARK_AGENT_SOURCE": "reasonix" }
      }
    ]
  }
}
```

说明：

- `UserPromptSubmit` 重置每回合通知状态；`Stop` 有 10 秒防抖（与 ZCode 相同）。
- 不设 `BARK_AGENT_SOURCE` 也能靠 payload 的 camelCase 字段自动识别。

#### Pi

Pi 采用扩展方式。一行安装到 `~/.pi/agent/extensions/`：

```bash
curl -fsSL https://raw.githubusercontent.com/YLongo/bark-notification/main/bark-notify-pi.ts \
  -o ~/.pi/agent/extensions/bark-notify-pi.ts
```

然后编辑该文件，把 `BARK_SCRIPT` 指向你本地的 `bark_notification.py`（文件顶部标了必改）：

```typescript
const BARK_SCRIPT = "/path/to/bark_notification.py";
```

重启 pi 或输入 `/reload` 生效。

说明：

- 通知内容：🍕 标题带会话名/项目名，副标题带项目与耗时，正文为最后一条 assistant 回复（自动压平空白并截断）。
- 环境变量由 pi 进程继承：从终端启动 pi 时，shell 里的 `BARK_BASE` 等配置自动生效，扩展无需额外配置。

#### OpenCode

OpenCode 没有原生通知配置，采用插件方式（官方文档也是以插件作为通知示例）。创建 `~/.config/opencode/plugins/notify.ts`（注意目录是复数 `plugins`）：

```typescript
import type { Plugin } from "@opencode-ai/plugin";

const NOTIFY_SCRIPT = "/path/to/bark_notification.py";

export const NotifyPlugin: Plugin = async ({ client, $ }) => {
  const notify = (type: string, message: string) => {
    // `source` 标签让脚本跳过字符串嗅探，直接选中 OpenCode 图标和标题
    const payload = JSON.stringify({ source: "opencode", title: "OpenCode", type, message });
    $`echo ${payload} | python3 ${NOTIFY_SCRIPT}`.quiet().catch(() => {});
  };

  return {
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        const { sessionID } = event.properties;
        const sessions = await client.session.list({ limit: 50 });
        const session = sessions.data?.find((s: { id: string }) => s.id === sessionID);
        if (!session || session.parentID) return;

        notify("session.idle", session.title || "Task completed");
      }

      if (event.type === "permission.asked") {
        const { permission, patterns } = event.properties;
        const detail = patterns.length ? `: ${patterns.join(", ")}` : "";
        notify("permission.asked", `${permission}${detail}`);
      }

      if (event.type === "question.asked" || event.type === "question.v2.asked") {
        for (const q of event.properties.questions) {
          const options = q.options?.length ? ` (${q.options.length} options)` : "";
          notify("question.asked", `${q.question}${options}`);
        }
      }
    },
  };
};
```

说明：

- `session.idle` 只对主会话推送（跳过子会话），正文取会话标题。
- `permission.asked` / `question.asked` 在 agent 需要你批准权限或回答问题时推送——手机 SSH 场景很好用。
- 若类型报错，在 `~/.config/opencode/package.json` 加依赖：`{"dependencies": {"@opencode-ai/plugin": "1.16.2"}}`。

只需要任务完成推送的话，最小版约十行：

```typescript
export const NotifyPlugin = async ({ $ }) => ({
  event: async ({ event }) => {
    if (event.type === "session.idle") {
      const payload = JSON.stringify({ source: "opencode", type: event.type });
      $`echo ${payload} | python3 /path/to/bark_notification.py`.quiet().catch(() => {});
    }
  },
});
```

（最小版不过滤子会话，子会话结束也会各推一条。）

## 可选：启用端到端加密

不配置加密也能正常使用（明文推送）。想加密通知内容，两侧各配置一次：

**手机侧（Bark App）**：首页 → **推送加密** → **加密设置**，算法选 `AES256`、模式选 `GCM`（padding 自动变为 noPadding），填入 32 位密钥。App 没有 IV 输入框——IV 由脚本侧配置，随每次推送携带。

**电脑侧**——写进配置文件（推荐，见「配置脚本」）：

```bash
echo 'BARK_ENCRYPTION_KEY=your-32-character-encryption-key' >> ~/.config/bark-notification/config
echo 'BARK_ENCRYPTION_IV=your-12char-iv' >> ~/.config/bark-notification/config
```

两侧的 KEY 必须一致；IV 只存在于脚本侧。长度须为 32 / 12 字节（ASCII 字符下即 32 / 12 个字符）——不对则警告并回退明文，不会静默失败。

没有现成密钥？一条命令随机生成：

```bash
python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(32))); print("".join(secrets.choice(a) for _ in range(12)))'
```

第一行是 KEY（填进 App 和配置文件），第二行是 IV（只填配置文件）。配置文件即时生效，无需重启终端。

详细说明参见官方文档：[Bark 推送加密](https://bark.day.app/#/encryption)。

## 工作原理

脚本根据 payload 结构自动识别触发通知的工具：

- **ZCode**：通过 `ZCODE_SESSION_ID`/`ZCODE_PROJECT_DIR` 环境变量、camelCase 重复字段（`hookEventName`/`transcriptPath`）、`agent_type` 或 `zcode-hook-*` 转录路径识别
- **Claude Code**：通过 `hook_event_name`、`session_id` 或 `transcript_path` 字段识别
- **Reasonix**：通过 `BARK_AGENT_SOURCE` 环境变量或 camelCase 字段（`sessionId`/`lastAssistantText`）识别
- **OpenCode**：通过会话类事件或标题中的 OpenCode 字样识别
- **Codex CLI**：其余 payload 的默认回退

调用方也可以在 payload 中带 `source` 字段（`"claude"`、`"opencode"`、`"reasonix"`、`"pi"`、`"zcode"`、`"codex"`）直接跳过识别。上文 OpenCode 插件就是这么做的。

每条通知包含：

- 工具专属标题和图标
- 事件类型作为副标题（无事件类型时省略）
- 最后一条 assistant 消息或事件摘要作为正文

## 测试

用标准库跑单元测试：

```sh
python3 -m unittest discover -s tests
```

可选的 `cryptography` 包只有加密形态的测试路径需要；其余用例没有它也能跑。

## 安全性

启用加密后，通知内容在本地用 AES-256-GCM 加密后才发出：

- Apple 推送服务无法读取内容
- Bark 服务器只存储密文
- 只有持有密钥的你的设备能解密

未启用加密时，通知内容以明文经 HTTPS 传输并经过 Bark 服务器——适合非敏感内容。

## 免责声明

本 README、脚本及配置文件由 AI 辅助生成。作者确认其功能完整并正在实际使用中。
