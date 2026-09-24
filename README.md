# 适用于 [Claude Code](https://code.claude.com/docs/en/overview)/[Codex CLI](https://github.com/openai/codex)/[OpenCode](https://opencode.ai)/[Pi](https://pi.dev)/[ZCode](https://zcode.z.ai) 的 Bark 通知

当你的 AI 编程 agent 完成任务时，通过 [Bark](https://github.com/Finb/Bark) 向你的 iOS 设备推送通知。

## 特性

- **统一脚本**：一个脚本无缝支持 Claude Code、Codex CLI、OpenCode、Pi、ZCode
- **可选端到端加密**：采用 AES-256-GCM 加密，Apple 和 Bark 服务器都无法读取你的通知内容

## 前置条件

- Python 3.x
- iOS 设备上安装 [Bark](https://apps.apple.com/app/id1403753865) App
- `cryptography` 库：`pip install cryptography`（macOS 也可 `brew install cryptography`）

## 安装配置

### 1. 配置 Bark 加密

1. 在 iOS 设备上打开 Bark App
2. 首页找到 **推送加密**，点击 **加密设置**
3. 算法选 `AES256`，模式选 `GCM`（padding 自动变为 noPadding），填入 **32 位密钥**——App 无需填 IV，IV 由脚本侧配置并随每次推送携带
4. 复制你的 Bark 推送 URL（形如 `https://api.day.app/YOUR_DEVICE_KEY`）

详细说明参见官方文档：[Bark 推送加密](https://bark.day.app/#/encryption)。

### 2. 配置脚本

脚本从环境变量读取配置——无需改动源码，仓库中也不会残留任何个人密钥。加到 shell 配置文件（`~/.zshrc` / `~/.bashrc`）中，所有 agent 的 hook 都会自动继承：

```bash
export BARK_BASE="https://api.day.app/YOUR_DEVICE_KEY"
export BARK_ENCRYPTION_KEY="your-32-character-encryption-key"   # 可选
export BARK_ENCRYPTION_IV="your-12char-iv"                       # 可选
```

- `BARK_BASE` —— 推送必需。未设置时脚本跳过 Bark 推送（stderr 会给出提示），本地 macOS 通知照常弹出。
- `BARK_ENCRYPTION_KEY` / `BARK_ENCRYPTION_IV` —— 可选；两者都设置且长度正确（32 / 12 字符）才启用 AES-256-GCM。长度不对则回退为明文并给出警告。

也可以在单个 hook 里内联设置，例如 Claude Code：

```json
"command": "BARK_BASE=https://api.day.app/YOUR_DEVICE_KEY python3 /path/to/bark_notification.py"
```

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

## 工作原理

脚本根据 payload 结构自动识别触发通知的工具：

- **ZCode**：通过 `ZCODE_SESSION_ID`/`ZCODE_PROJECT_DIR` 环境变量、camelCase 重复字段（`hookEventName`/`transcriptPath`）、`agent_type` 或 `zcode-hook-*` 转录路径识别
- **Claude Code**：通过 `hook_event_name`、`session_id` 或 `transcript_path` 字段识别
- **OpenCode**：通过会话类事件或标题中的 OpenCode 字样识别
- **Codex CLI**：其余 payload 的默认回退

调用方可以在 payload 中带 `source` 字段（`"claude"`、`"opencode"`、`"zcode"`、`"codex"`）直接跳过识别。上文 OpenCode 插件就是这么做的；Claude Code 和 Codex CLI 也可以在 hook payload 里透传该标签。

每条通知包含：
- 工具专属标题和图标
- 事件类型作为副标题
- 最后一条 assistant 消息或事件摘要作为正文

## 测试

用标准库跑单元测试：

```sh
python3 -m unittest discover -s tests
```

可选的 `cryptography` 包只有加密形态的测试路径需要；其余用例没有它也能跑。

## 安全性

配置了 `BARK_ENCRYPTION_KEY` / `BARK_ENCRYPTION_IV` 时，通知内容在本地用 AES-256-GCM 加密后才发送。加密保证：

- Apple 推送服务无法读取内容
- Bark 服务器只存储加密数据
- 只有持有对应密钥的你的设备能解密通知

## 免责声明

本 README、脚本及配置文件由 AI 辅助生成。作者确认其功能完整并正在实际使用中。
