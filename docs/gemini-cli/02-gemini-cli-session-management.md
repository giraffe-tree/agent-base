# Session 管理（gemini-cli）

本文基于 `./gemini-cli` 源码，解释 gemini-cli 如何实现 session 创建、消息持久化、多轮对话恢复、以及自动清理策略。
为适配"先看全貌再看细节"的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 Session 生命周期流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 创建或恢复 Session                                       │
│  ┌─────────────────┐                                            │
│  │ 用户输入 /resume│ 或 │ 新对话开始                           │
│  │ 或指定 sessionId│      │ UUID 生成                          │
│  └────────┬────────┘      │ timestamp 文件名                    │
└───────────┼───────────────┼─────────────────────────────────────┘
            │               │
            ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│  INITIALIZE: Session 初始化                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ ChatRecordingService.initialize()      │                     │
│  │  ├── 恢复模式?                         │                     │
│  │  │   ├── Yes: 使用现有文件路径         │                     │
│  │  │   └── No:  创建新会话文件           │                     │
│  │  │       session-<timestamp>-<uuid>.json│                    │
│  │  └── 写入初始 ConversationRecord      │                     │
│  │       ├── sessionId                   │                     │
│  │       ├── projectHash                 │                     │
│  │       ├── startTime                   │                     │
│  │       └── messages: []                │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  RUNTIME: 对话执行与持久化                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ 每轮对话                               │                     │
│  │  ├── 用户输入                          │                     │
│  │  │   └── appendMessage(type: 'user')   │                     │
│  │  ├── Gemini API 调用                   │                     │
│  │  ├── 流式响应处理                      │                     │
│  │  │   ├── OutputItemAdded              │                     │
│  │  │   ├── OutputTextDelta              │                     │
│  │  │   └── OutputItemDone               │                     │
│  │  ├── 工具调用执行                      │                     │
│  │  │   └── tool-outputs 目录存储         │                     │
│  │  └── appendMessage(type: 'gemini')    │                     │
│  │       └── 更新 lastUpdated            │                     │
│  │  ◄── 循环直到用户退出                  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  RESUME: Session 恢复（可选）                                     │
│  ┌────────────────────────────────────────┐                     │
│  │ /resume 命令                           │                     │
│  │  ├── 查找 session 文件                 │                     │
│  │  ├── convertSessionToHistoryFormats()  │                     │
│  │  │   ├── UI history 格式               │                     │
│  │  │   └── Gemini client history 格式    │                     │
│  │  ├── 恢复工作区目录                    │                     │
│  │  └── client.resumeChat()              │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CLEANUP: 自动清理                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ cleanupExpiredSessions()               │                     │
│  │  ├── 基于 maxAge 删除老文件            │                     │
│  │  ├── 基于 maxCount 保留最近 N 个       │                     │
│  │  ├── 清理 activity logs                │                     │
│  │  └── 清理 tool-outputs 目录            │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流
```

### 1.2 数据流与存储结构图

```text
┌────────────────────────────────────────────────────────────────────┐
│ [A] 消息流向                                                        │
└────────────────────────────────────────────────────────────────────┘

   用户输入              Gemini API              工具执行
       │                     │                      │
       ▼                     ▼                      ▼
┌─────────────┐     ┌─────────────────┐    ┌───────────────┐
│  UI Layer   │────▶│  Gemini Client  │───▶│  Tool System  │
│             │◄────│                 │◄───│               │
└──────┬──────┘     └────────┬────────┘    └───────┬───────┘
       │                     │                      │
       │              ┌──────┴──────┐              │
       │              ▼             ▼              │
       │     ┌─────────────┐ ┌─────────────┐      │
       │     │   History   │ │  Token Trk  │      │
       │     │   Manager   │ │             │      │
       │     └─────────────┘ └─────────────┘      │
       │                     │                      │
       └─────────────────────┼──────────────────────┘
                             ▼
              ┌─────────────────────────┐
              │  ChatRecordingService   │
              │  - 持久化到 JSON 文件   │
              │  - 自动保存             │
              └───────────┬─────────────┘
                          ▼
              ┌─────────────────────────┐
              │  ~/.gemini/tmp/<hash>/  │
              │  └── chats/*.json       │
              └─────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ [B] 存储目录结构                                                    │
└────────────────────────────────────────────────────────────────────┘

~/.gemini/tmp/
├── <project_hash>/                    # 项目隔离
│   ├── chats/
│   │   └── session-<timestamp>-<id>.json   # 会话文件
│   ├── logs/
│   │   └── session-<id>.jsonl        # 活动日志
│   └── tool-outputs/
│       └── session-<id>/             # 工具输出文件
│           ├── <tool_call_id>.txt
│           └── ...
└── ...

┌────────────────────────────────────────────────────────────────────┐
│ [C] ConversationRecord 结构                                         │
└────────────────────────────────────────────────────────────────────┘

ConversationRecord
├── sessionId: string                  # UUID
├── projectHash: string                # 项目标识
├── startTime: string                  # ISO 时间
├── lastUpdated: string                # 最后更新时间
├── summary?: string                   # 会话摘要
├── directories?: string[]             # 工作区目录
└── messages: MessageRecord[]
    ├── id: string
    ├── timestamp: string
    ├── content: PartListUnion         # 消息内容
    ├── displayContent?: PartListUnion # 显示内容
    └── type: 'user' | 'gemini' | 'info' | 'error' | 'warning'
        └── (type='gemini' 时)
            ├── toolCalls?: ToolCallRecord[]
            ├── thoughts?: ThoughtSummary[]
            ├── tokens?: TokensSummary
            └── model?: string

图例: ───▶ 流向  ┌─┐ 模块/数据结构
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 session 从创建到清理的完整流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `3`（知道存储结构和核心服务）。
- **10 分钟版**：通读 `3~7`（能定位 session 相关问题）。

### 2.1 一句话定义

gemini-cli 的 Session 是**"项目隔离的文件持久化单元"**：每个 session 以 JSON 文件形式存储在项目隔离的目录中，自动记录所有消息、工具调用和 token 使用情况，支持基于时间戳的 session 选择和自动清理。

---

## 3. 核心组件

### 3.1 Session ID 生成

**文件**: `packages/core/src/utils/session.ts`

```typescript
export const sessionId = randomUUID();
```

使用 Node.js 的 `crypto.randomUUID()` 生成标准 UUID。

### 3.2 ChatRecordingService

**文件**: `packages/core/src/services/chatRecordingService.ts`

```typescript
export class ChatRecordingService {
  private conversationFile: string | null = null;
  private sessionId: string;
  private projectHash: string;

  initialize(resumedSessionData?: ResumedSessionData): void {
    if (resumedSessionData) {
      // 恢复模式：使用现有文件
      this.conversationFile = resumedSessionData.filePath;
      this.sessionId = resumedSessionData.conversation.sessionId;
    } else {
      // 新模式：创建带时间戳的文件
      const timestamp = new Date().toISOString()
        .slice(0, 16)
        .replace(/:/g, '-');
      const filename = `${SESSION_FILE_PREFIX}${timestamp}-${this.sessionId.slice(0, 8)}.json`;
      this.conversationFile = path.join(chatsDir, filename);

      this.writeConversation({
        sessionId: this.sessionId,
        projectHash: this.projectHash,
        startTime: new Date().toISOString(),
        lastUpdated: new Date().toISOString(),
        messages: [],
      });
    }
  }
}
```

**关键特性**:
- **自动降级**: 磁盘满（ENOSPC）时禁用记录，但 CLI 继续运行
- **去重**: 按 sessionId 去重，保留最新版本
- **损坏检测**: 自动检测并过滤损坏的 session 文件

---

## 4. Session 持久化详解

### 4.1 消息记录结构

```typescript
export interface ConversationRecord {
  sessionId: string;
  projectHash: string;
  startTime: string;
  lastUpdated: string;
  messages: MessageRecord[];
  summary?: string;
  directories?: string[];
}

export type MessageRecord = BaseMessageRecord & ConversationRecordExtra;

export interface BaseMessageRecord {
  id: string;
  timestamp: string;
  content: PartListUnion;
  displayContent?: PartListUnion;
}

type ConversationRecordExtra =
  | { type: 'user' | 'info' | 'error' | 'warning' }
  | {
      type: 'gemini';
      toolCalls?: ToolCallRecord[];
      thoughts?: Array<ThoughtSummary & { timestamp: string }>;
      tokens?: TokensSummary | null;
      model?: string;
    };
```

### 4.2 存储位置

```
~/.gemini/tmp/<project_hash>/
├── chats/session-<timestamp>-<sessionId>.json    # 会话数据
├── logs/session-<sessionId>.jsonl                # 活动日志
└── tool-outputs/session-<sessionId>/             # 工具输出
```

---

## 5. Session 恢复机制

### 5.1 恢复流程

**文件**: `packages/core/src/core/client.ts`

```typescript
async resumeChat(
  history: Content[],
  resumedSessionData?: ResumedSessionData,
): Promise<void> {
  this.chat = await this.startChat(history, resumedSessionData);
  this.updateTelemetryTokenCount();
}
```

**文件**: `packages/cli/src/ui/hooks/useSessionResume.ts`

```typescript
export function useSessionResume({
  config,
  historyManager,
  resumedSessionData,
}: UseSessionResumeParams) {
  const loadHistoryForResume = useCallback(
    async (uiHistory, clientHistory, resumedData) => {
      // 1. 清空当前历史
      historyManagerRef.current.clearItems();

      // 2. 加载 UI 历史
      uiHistory.forEach((item, index) => {
        historyManagerRef.current.addItem(item, index, true);
      });

      // 3. 恢复工作区目录
      if (resumedData.conversation.directories) {
        workspaceContext.addDirectories(resumedData.conversation.directories);
      }

      // 4. 恢复 Gemini client
      await config.getGeminiClient()?.resumeChat(clientHistory, resumedData);
    }
  );
}
```

### 5.2 Session 选择方式

**文件**: `packages/cli/src/utils/sessionUtils.ts`

```typescript
// 三种选择方式
1. By UUID:   直接匹配 sessionId
2. By Index:  数字索引（1-based）从排序列表中选择
3. By Keyword: "latest" 选择最近会话
```

### 5.3 历史格式转换

```typescript
export function convertSessionToHistoryFormats(
  messages: ConversationRecord['messages'],
): {
  uiHistory: HistoryItemWithoutId[];
  clientHistory: Array<{ role: 'user' | 'model'; parts: Part[] }>;
} {
  // 转换为 UI 显示格式和 Gemini API 格式
  // - 过滤掉系统消息
  // - 处理工具调用和函数响应
  // - 保留消息角色和内容
}
```

---

## 6. 上下文管理

### 6.1 上下文压缩

- **自动压缩**: 接近 token 限制时自动压缩
- **工具输出掩码**: 大工具输出被掩码以节省 token
- **循环检测**: 防止对话无限循环

### 6.2 IDE 上下文集成

可选的 IDE 上下文注入，提供更丰富的开发环境信息。

---

## 7. 命令历史管理

### 7.1 Input History Store

**文件**: `packages/cli/src/ui/hooks/useInputHistoryStore.ts`

```typescript
export function useInputHistoryStore(): UseInputHistoryStoreReturn {
  const [inputHistory, setInputHistory] = useState<string[]>([]);

  // 合并当前会话 + 过去会话消息
  // 连续相同消息去重
  // 倒序排列（最旧在前）用于 UI 显示
}
```

**特性**:
- 跨会话持久化
- 连续相同输入去重
- 当前与历史会话分离

---

## 8. 自动清理策略

### 8.1 保留设置

**文件**: `packages/cli/src/config/settingsSchema.ts`

```typescript
sessionRetention: {
  enabled: boolean;        // 启用自动清理
  maxAge: string;         // 如 "30d", "7d", "24h"
  maxCount: number;       // 最大保留会话数
  minRetention: string;   // 安全限制（默认: "1d"）
  warningAcknowledged: boolean;
}
```

### 8.2 清理流程

**文件**: `packages/cli/src/utils/sessionCleanup.ts`

```typescript
export async function cleanupExpiredSessions(
  config: Config,
  settings: Settings,
): Promise<CleanupResult> {
  // 识别需要删除的会话：
  // 1. 损坏的文件
  // 2. 基于 maxAge 的老文件
  // 3. 基于 maxCount 的超出数量文件
  // 4. 保护当前会话

  // 同时清理：
  // - Activity logs
  // - Tool output 目录
}
```

---

## 9. 排障速查

| 问题 | 检查点 |
|------|--------|
| session 文件未创建 | 检查 `~/.gemini/tmp/` 目录权限和磁盘空间 |
| 恢复失败 | 检查 session 文件是否损坏，查看 `convertSessionToHistoryFormats` 错误 |
| 工具输出丢失 | 检查 `tool-outputs/` 目录是否存在 |
| 历史记录不完整 | 查看 `ChatRecordingService` 的降级日志 |
| session 过多 | 调整 `sessionRetention` 配置，运行清理 |

---

## 10. 架构特点总结

1. **项目隔离**: 每个项目有独立的 session 存储空间
2. **时间戳命名**: 文件名包含可读时间，便于人工识别
3. **双格式历史**: UI 历史与 Client 历史分离，支持不同需求
4. **自动降级**: 磁盘满时继续运行，牺牲可恢复性保证可用性
5. **自管理**: 基于策略的自动清理，无需用户手动维护
6. **只追加**: Session 文件只追加，天然支持增量备份
7. **富元数据**: 记录工具调用、token 使用、思考过程等完整信息
