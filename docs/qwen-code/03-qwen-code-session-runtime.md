# Session 运行时（Qwen Code）

本文分析 Qwen Code 的 Session 运行时机制，包括会话存储格式、恢复流程和管理接口。

---

## 1. 先看全局（流程图）

### 1.1 Session 生命周期

```text
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION 创建                                                        │
│  ┌─────────────────────────────────────────┐                        │
│  │ 用户输入第一条消息                       │                        │
│  │   │                                     │                        │
│  │   └──► 生成 sessionId (UUID)           │                        │
│  │   │                                     │                        │
│  │   └──► ChatRecordingService 启动       │                        │
│  │          ├── 创建 ~/.qwen/tmp/<project>/chats/<sessionId>.jsonl
│  │          └── 写入第一条 ChatRecord     │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION 运行                                                        │
│  ┌─────────────────────────────────────────┐                        │
│  │ 每轮对话                                │                        │
│  │   ├── 用户消息 ──► recordUserMessage() │                        │
│  │   └── 模型响应 ──► recordModelMessage()│                        │
│  │                                         │                        │
│  │ ChatRecord 包含:                        │                        │
│  │   - type: 'user' | 'model' | 'tool'    │                        │
│  │   - uuid, parentUuid, message          │                        │
│  │   - timestamp, prompt_id               │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION 恢复                                                        │
│  ┌─────────────────────────────────────────┐                        │
│  │ gemini --resume [sessionId]            │                        │
│  │   │                                     │                        │
│  │   └──► 扫描 chats/ 目录                │                        │
│  │   │                                     │                        │
│  │   └──► 解析 JSONL 文件                 │                        │
│  │   │                                     │                        │
│  │   └──► buildApiHistoryFromConversation │                        │
│  │          ├── 重建 Content[]            │                        │
│  │          └── 恢复 GeminiChat 状态      │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

图例: JSONL = JSON Lines 格式，每行一条记录
```

### 1.2 Session 文件结构

```
~/.qwen/
└── tmp/
    └── <project_hash>/
        └── chats/
            ├── <session-id-1>.jsonl    # Session 文件
            ├── <session-id-2>.jsonl
            └── ...

Session 文件内容 (JSONL 格式):
{"type":"session_start","timestamp":"2025-02-23T10:00:00Z","cwd":"/path/to/project","gitBranch":"main"}
{"type":"user","uuid":"msg-001","timestamp":"2025-02-23T10:00:01Z","message":{"role":"user","parts":[{"text":"Hello"}]}}
{"type":"model","uuid":"msg-002","parentUuid":"msg-001","timestamp":"...","message":{"role":"model","parts":[{"text":"Hi there!"}]}}
{"type":"tool_call","uuid":"msg-003","parentUuid":"msg-002","toolCall":{"name":"read_file","args":{"path":"package.json"}}}
...
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道 Session 以 JSONL 格式存储在 `~/.qwen/tmp/<project>/chats/`。
- **3 分钟版**：看 `1.1` + `1.2` + `3.1` 节，了解 ChatRecord 结构和恢复流程。
- **10 分钟版**：通读全文，掌握 SessionService API 和会话管理机制。

### 2.1 一句话定义

Qwen Code 的 Session 运行时采用「**JSONL 文件 + UUID 消息树**」结构，支持会话持久化、增量恢复和跨会话历史检索。

---

## 3. 核心组件

### 3.1 ChatRecordingService

✅ **Verified**: `qwen-code/packages/core/src/services/chatRecordingService.ts`

```typescript
export class ChatRecordingService {
  private stream: fs.WriteStream | null = null;
  private messageUuids: Set<string> = new Set();

  constructor(
    private readonly sessionId: string,
    private readonly projectHash: string,
  ) {}

  // 记录用户消息
  recordUserMessage(content: Content, prompt_id: string): string {
    const uuid = generateUuid();
    const record: ChatRecord = {
      type: 'user',
      uuid,
      timestamp: new Date().toISOString(),
      message: content,
      prompt_id,
    };
    this.writeRecord(record);
    return uuid;
  }

  // 记录模型消息
  recordModelMessage(
    content: Content,
    parentUuid: string,
    prompt_id: string,
  ): string {
    const uuid = generateUuid();
    const record: ChatRecord = {
      type: 'model',
      uuid,
      parentUuid,
      timestamp: new Date().toISOString(),
      message: content,
      prompt_id,
    };
    this.writeRecord(record);
    return uuid;
  }

  private writeRecord(record: ChatRecord): void {
    if (!this.stream) {
      this.stream = fs.createWriteStream(this.getSessionFilePath(), {
        flags: 'a',  // 追加模式
      });
    }
    this.stream.write(JSON.stringify(record) + '\n');
  }
}
```

### 3.2 SessionService

✅ **Verified**: `qwen-code/packages/core/src/services/sessionService.ts:128`

```typescript
export class SessionService {
  constructor(cwd: string) {
    this.storage = new Storage(cwd);
    this.projectHash = getProjectHash(cwd);
  }

  // 列出会话（支持分页）
  async listSessions(options: ListSessionsOptions = {}): Promise<ListSessionsResult> {
    const files = await fs.promises.readdir(this.getChatsDir());
    const sessions: SessionListItem[] = [];

    for (const file of files) {
      if (!SESSION_FILE_PATTERN.test(file)) continue;

      const filePath = path.join(this.getChatsDir(), file);
      const stats = await fs.promises.stat(filePath);
      const sessionId = path.basename(file, '.jsonl');

      // 提取第一条用户提示
      const prompt = await this.extractFirstPrompt(filePath);

      sessions.push({
        sessionId,
        cwd: this.getCwdFromSession(filePath),
        startTime: new Date(stats.birthtime).toISOString(),
        mtime: stats.mtime.getTime(),
        prompt,
        filePath,
        messageCount: await this.countMessages(filePath),
      });
    }

    // 按 mtime 降序排列
    sessions.sort((a, b) => b.mtime - a.mtime);

    // 分页逻辑
    const cursor = options.cursor;
    const size = options.size || 20;
    const startIndex = cursor
      ? sessions.findIndex(s => s.mtime < cursor)
      : 0;

    return {
      items: sessions.slice(startIndex, startIndex + size),
      nextCursor: sessions[startIndex + size - 1]?.mtime,
      hasMore: sessions.length > startIndex + size,
    };
  }

  // 加载完整会话（用于恢复）
  async loadSession(sessionId: string): Promise<ResumedSessionData | null> {
    const filePath = this.getSessionFilePath(sessionId);
    const conversation = await this.parseSessionFile(filePath);

    if (!conversation) return null;

    // 找到最后一条完成的 message uuid
    const lastCompletedUuid = conversation.messages.length > 0
      ? conversation.messages[conversation.messages.length - 1].uuid
      : null;

    return {
      conversation,
      filePath,
      lastCompletedUuid,
    };
  }

  // 删除会话
  async removeSession(sessionId: string): Promise<void> {
    const filePath = this.getSessionFilePath(sessionId);
    await fs.promises.unlink(filePath);
  }
}
```

### 3.3 会话恢复流程

✅ **Verified**: `qwen-code/packages/core/src/core/client.ts:97`

```typescript
async initialize() {
  this.lastPromptId = this.config.getSessionId();

  // 检查是否从之前的会话恢复
  const resumedSessionData = this.config.getResumedSessionData();
  if (resumedSessionData) {
    // 重建 UI 遥测
    replayUiTelemetryFromConversation(resumedSessionData.conversation);

    // 转换为 API 历史格式
    const resumedHistory = buildApiHistoryFromConversation(
      resumedSessionData.conversation,
    );
    this.chat = await this.startChat(resumedHistory);
  } else {
    this.chat = await this.startChat();
  }
}
```

### 3.4 构建 API 历史

✅ **Verified**: `qwen-code/packages/core/src/services/sessionService.ts`

```typescript
export function buildApiHistoryFromConversation(
  conversation: ConversationRecord,
): Content[] {
  const history: Content[] = [];

  for (const record of conversation.messages) {
    if (record.type === 'user' || record.type === 'model') {
      history.push(record.message as Content);
    }
    // Tool calls 和 tool responses 作为 functionCall/functionResponse parts
    // 嵌套在 model/user messages 中
  }

  return history;
}
```

---

## 4. 数据结构

### 4.1 ChatRecord 类型

```typescript
interface ChatRecord {
  type: 'user' | 'model' | 'tool_call' | 'tool_response' | 'compression' | 'session_start';
  uuid: string;
  parentUuid?: string;       // 用于构建消息树
  timestamp: string;
  message?: Content;         // user/model 消息内容
  toolCall?: ToolCallInfo;   // tool_call 类型
  toolResponse?: ToolResponseInfo;  // tool_response 类型
  prompt_id: string;
  metadata?: Record<string, unknown>;
}
```

### 4.2 ConversationRecord 类型

```typescript
interface ConversationRecord {
  sessionId: string;
  projectHash: string;
  startTime: string;
  lastUpdated: string;
  messages: ChatRecord[];    // 按时间顺序排列
}
```

### 4.3 SessionListItem 类型

```typescript
interface SessionListItem {
  sessionId: string;
  cwd: string;               // 工作目录
  startTime: string;         // ISO 8601 格式
  mtime: number;             // 用于分页
  prompt: string;            // 第一条用户提示（截断）
  filePath: string;
  messageCount: number;
  gitBranch?: string;
}
```

---

## 5. 存储路径

### 5.1 路径计算

```typescript
// 基础目录
~/.qwen/tmp/<project_hash>/

// project_hash 计算
function getProjectHash(cwd: string): string {
  // 基于工作目录路径的哈希
  return createHash('md5').update(cwd).digest('hex').slice(0, 16);
}

// Session 文件路径
~/.qwen/tmp/<project_hash>/chats/<session_id>.jsonl

// 其他存储
~/.qwen/tmp/<project_hash>/checkpoints/   # Checkpoint 文件
~/.qwen/tmp/<project_hash>/telemetry/     # 遥测数据
```

### 5.2 文件命名

```typescript
// Session 文件命名规则
const SESSION_FILE_PATTERN = /^[0-9a-fA-F-]{32,36}\.jsonl$/;

// sessionId 生成
const sessionId = randomUUID().replace(/-/g, '');  // 32位十六进制
```

---

## 6. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| Session 未保存 | 检查 ChatRecordingService 初始化 | `chatRecordingService.ts` |
| 恢复后历史丢失 | 检查 buildApiHistoryFromConversation | `sessionService.ts` |
| 列表加载慢 | 检查 MAX_FILES_TO_PROCESS 限制 | `sessionService.ts:106` |
| 消息顺序错乱 | 检查 parentUuid 链 | `chatRecordingService.ts` |
| 磁盘空间不足 | 清理旧 sessions | `cleanupCheckpoints()` |
| 会话文件损坏 | 检查 JSON 解析错误处理 | `sessionService.ts` |

---

## 7. 架构特点

### 7.1 JSONL 格式优势

```
优点:
✓ 追加写入高效（无需读取整个文件）
✓ 损坏时部分可恢复
✓ 便于流式处理
✓ 人类可读

缺点:
✗ 随机访问慢
✗ 需要扫描整个文件才能计数
```

### 7.2 消息树结构

```
Session 不是线性数组，而是树形结构:

msg-001 (user: "Hello")
    └── msg-002 (model: "Hi!")
            ├── msg-003 (tool_call: read_file)
            │       └── msg-004 (tool_response)
            │               └── msg-005 (model: "Here's the content...")
            └── msg-006 (user: "Thanks")
                    └── ...

parentUuid 用于构建这个树形结构
```

### 7.3 分页机制

```typescript
// 基于 mtime 的游标分页
const result = await sessionService.listSessions({
  cursor: lastMtime,  // 上一页最后一条的 mtime
  size: 20,           // 每页数量
});

// 返回结果包含 nextCursor 用于获取下一页
```

---

## 8. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 存储格式 | JSONL | ✅ 相同 |
| 消息树 | 支持 | ✅ 继承 |
| 分页列表 | 支持 | ✅ 继承 |
| Session ID | UUID | ✅ 相同 |
| 存储路径 | ~/.gemini/tmp | ~/.qwen/tmp |

---

## 9. 总结

Qwen Code 的 Session 运行时设计特点：

1. **JSONL 持久化** - 高效追加写入，部分可恢复
2. **UUID 消息树** - 支持分支和复杂对话结构
3. **分页查询** - 基于 mtime 游标的高效列表
4. **完整恢复** - API 历史重建，状态无缝恢复
5. **项目隔离** - 基于 project_hash 的目录隔离
