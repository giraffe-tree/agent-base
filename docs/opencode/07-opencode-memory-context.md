# Memory Context 管理（opencode）

本文基于 `./opencode` 源码，解释 OpenCode 如何实现 Session、Message、Part 的层级化上下文管理，包括 SQLite 持久化、Streaming Architecture 和上下文压缩。

---

## 1. 先看全局（流程图）

### 1.1 Session → Message → Part 层级结构

```text
┌─────────────────────────────────────────────────────────────────┐
│  Session (会话)                                                   │
│  ┌────────────────────────────────────────┐                     │
│  │ id: string (primary key)               │                     │
│  │ project_id: string (foreign key)       │                     │
│  │ parent_id: string? (分支会话)          │                     │
│  │ title: string                          │                     │
│  │ summary_files: number                  │                     │
│  │ revert: JSON? (回滚信息)               │                     │
│  │ permission: JSON (权限规则)            │                     │
│  └────┬───────────────────────────────────┘                     │
└───────┼─────────────────────────────────────────────────────────┘
        │ 1:N
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Message (消息)                                                   │
│  ┌────────────────────────────────────────┐                     │
│  │ id: string (primary key)               │                     │
│  │ session_id: string (foreign key)       │                     │
│  │ data: JSON (InfoData)                  │                     │
│  │  ├── role: "user" | "assistant"        │                     │
│  │  └── content: Info[]                   │                     │
│  └────┬───────────────────────────────────┘                     │
└───────┼─────────────────────────────────────────────────────────┘
        │ 1:N
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Part (内容片段)                                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ id: string (primary key)               │                     │
│  │ message_id: string (foreign key)       │                     │
│  │ session_id: string (denormalized)      │                     │
│  │ data: JSON (PartData)                  │                     │
│  │  ├── type: "text" | "snapshot" |       │                     │
│  │  │         "file" | "reasoning"        │                     │
│  │  └── content (type-specific)           │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 数据流与持久化流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  用户输入 / AI 输出                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ UIMessage: { role, content, parts[] }  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  转换为 Model Messages                                            │
│  ┌────────────────────────────────────────┐                     │
│  │ convertToModelMessages()               │                     │
│  │  ├── ProviderTransform.apply()         │                     │
│  │  └── 转换为 Provider 特定格式          │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  SQLite + Drizzle ORM 持久化                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ Database.insert(MessageTable)          │                     │
│  │ Database.insert(PartTable)             │                     │
│  │                                        │                     │
│  │ 关联:                                  │                     │
│  │ - message.session_id → session.id      │                     │
│  │ - part.message_id → message.id         │                     │
│  │ - part.session_id → session.id (反范化)│                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道三层层级结构和 Part 类型多样性）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道数据库 Schema、Streaming 架构和压缩）。
- **10 分钟版**：通读全文（能理解完整的上下文管理和持久化机制）。

### 2.1 一句话定义

OpenCode 的 Memory Context 采用"**三层层级 + SQLite 持久化 + Streaming 架构**"的设计：通过 Session → Message → Part 的三级结构组织对话数据，使用 SQLite + Drizzle ORM 进行类型安全的持久化，并基于 AsyncLocalStorage 和 Event Bus 实现响应式的流式处理。

---

## 3. 核心组件详解

### 3.1 数据库 Schema (Drizzle ORM)

**文件**: `packages/opencode/src/session/session.sql.ts`

```typescript
import { sqliteTable, text, integer, index, primaryKey } from "drizzle-orm/sqlite-core"

// Session 表
export const SessionTable = sqliteTable(
  "session",
  {
    id: text().primaryKey(),
    project_id: text()
      .notNull()
      .references(() => ProjectTable.id, { onDelete: "cascade" }),
    parent_id: text(),  // 支持会话分支
    slug: text().notNull(),
    directory: text().notNull(),
    title: text().notNull(),
    version: text().notNull(),
    share_url: text(),
    // 摘要统计
    summary_additions: integer(),
    summary_deletions: integer(),
    summary_files: integer(),
    summary_diffs: text({ mode: "json" }).$type<Snapshot.FileDiff[]>(),
    // 回滚信息
    revert: text({ mode: "json" }).$type<{ messageID, partID, snapshot, diff }>(),
    // 权限规则
    permission: text({ mode: "json" }).$type<PermissionNext.Ruleset>(),
    ...Timestamps,  // created_at, updated_at
    time_compacting: integer(),  // 压缩时间戳
    time_archived: integer(),    // 归档时间戳
  },
  (table) => [
    index("session_project_idx").on(table.project_id),
    index("session_parent_idx").on(table.parent_id),
  ],
)

// Message 表
export const MessageTable = sqliteTable(
  "message",
  {
    id: text().primaryKey(),
    session_id: text()
      .notNull()
      .references(() => SessionTable.id, { onDelete: "cascade" }),
    ...Timestamps,
    data: text({ mode: "json" }).notNull().$type<InfoData>(),
  },
  (table) => [index("message_session_idx").on(table.session_id)],
)

// Part 表
export const PartTable = sqliteTable(
  "part",
  {
    id: text().primaryKey(),
    message_id: text()
      .notNull()
      .references(() => MessageTable.id, { onDelete: "cascade" }),
    session_id: text().notNull(),  // 反范化，便于查询
    ...Timestamps,
    data: text({ mode: "json" }).notNull().$type<PartData>(),
  },
  (table) => [
    index("part_message_idx").on(table.message_id),
    index("part_session_idx").on(table.session_id),
  ],
)

// Todo 表（会话内任务）
export const TodoTable = sqliteTable(
  "todo",
  {
    session_id: text()
      .notNull()
      .references(() => SessionTable.id, { onDelete: "cascade" }),
    content: text().notNull(),
    status: text().notNull(),
    priority: text().notNull(),
    position: integer().notNull(),
    ...Timestamps,
  },
  (table) => [
    primaryKey({ columns: [table.session_id, table.position] }),
    index("todo_session_idx").on(table.session_id),
  ],
)
```

### 3.2 Part 类型系统

**文件**: `packages/opencode/src/session/message-v2.ts`

```typescript
export namespace MessageV2 {
  // Part 基础类型
  const PartBase = z.object({
    id: z.string(),
    sessionID: z.string(),
    messageID: z.string(),
  })

  // 文本片段
  export const TextPart = PartBase.extend({
    type: z.literal("text"),
    text: z.string(),
    synthetic: z.boolean().optional(),  // 是否由系统生成
    ignored: z.boolean().optional(),    // 是否被忽略
    time: z.object({ start: z.number(), end: z.number().optional() }),
    metadata: z.record(z.string(), z.any()).optional(),
  })

  // 快照片段（代码状态）
  export const SnapshotPart = PartBase.extend({
    type: z.literal("snapshot"),
    snapshot: z.string(),  // 快照 ID
  })

  // 代码补丁片段
  export const PatchPart = PartBase.extend({
    type: z.literal("patch"),
    hash: z.string(),
    files: z.string().array(),
  })

  // 文件引用片段
  export const FilePart = PartBase.extend({
    type: z.literal("file"),
    path: z.string(),
    content: z.string().optional(),
    source: z.object({
      type: z.literal("file") | z.literal("symbol"),
      path: z.string(),
      text: z.object({ value: z.string(), start: z.number(), end: z.number() }),
    }),
  })

  // 推理片段
  export const ReasoningPart = PartBase.extend({
    type: z.literal("reasoning"),
    text: z.string(),
    metadata: z.record(z.string(), z.any()).optional(),
    time: z.object({ start: z.number(), end: z.number().optional() }),
  })
}
```

---

## 4. Streaming Architecture

### 4.1 AsyncLocalStorage 上下文传递

```typescript
import { AsyncLocalStorage } from "async_hooks"

// 创建 AsyncLocalStorage 实例
const sessionStorage = new AsyncLocalStorage<SessionContext>()

// 在异步调用链中传递上下文
async function processMessage(message: Message) {
  return sessionStorage.run({ sessionId: message.sessionId }, async () => {
    // 所有内部调用都可以访问 sessionStorage.getStore()
    await generateResponse()
    await persistToDatabase()
  })
}

// 在任意深度获取上下文
function logActivity(activity: string) {
  const context = sessionStorage.getStore()
  console.log(`[${context?.sessionId}] ${activity}`)
}
```

### 4.2 Event Bus 架构

```typescript
// 定义事件类型
export const BusEvent = {
  MessageCreated: "message:created",
  PartUpdated: "part:updated",
  SessionCompacted: "session:compacted",
  ContextOverflow: "context:overflow",
} as const

// 发布/订阅
eventBus.emit(BusEvent.MessageCreated, { messageId, sessionId })

eventBus.on(BusEvent.ContextOverflow, async ({ sessionId }) => {
  await triggerCompaction(sessionId)
})
```

---

## 5. 上下文压缩 (Context Compaction)

### 5.1 自动压缩触发

**文件**: `packages/app/src/context/global-sync/session-trim.ts`

```typescript
export async function checkAndCompactSession(sessionId: string) {
  const session = await loadSession(sessionId)
  const stats = await calculateContextStats(session)

  // 触发条件
  if (stats.estimatedTokens > CONTEXT_THRESHOLD * 0.8) {
    await compactSession(session, {
      strategy: "summarize",
      preserveRecent: 10,  // 保留最近 10 条消息
    })
  }
}
```

### 5.2 Prune 策略

```typescript
type PruneStrategy =
  | { type: "remove_reasoning" }     // 移除推理内容
  | { type: "remove_file_content" }  // 移除文件内容，保留引用
  | { type: "summarize"; messages: number }  // 总结旧消息
  | { type: "archive"; olderThan: Date }     // 归档旧消息

async function pruneSession(
  sessionId: string,
  strategies: PruneStrategy[],
): Promise<PruneResult> {
  for (const strategy of strategies) {
    switch (strategy.type) {
      case "remove_reasoning":
        await removeReasoningParts(sessionId)
        break
      case "summarize":
        await summarizeOldMessages(sessionId, strategy.messages)
        break
      // ...
    }
  }
}
```

### 5.3 压缩时序

```
┌─────────────────────────────────────────┐
│ 压缩前                                   │
│ [Msg 1] [Msg 2] ... [Msg 95] [Msg 96]   │
│  旧 ───────────────────────────────→ 新  │
└─────────────────────────────────────────┘
                    │
                    ▼ 总结 Msg 1-90
┌─────────────────────────────────────────┐
│ 压缩后                                   │
│ [Summary] [Msg 91] [Msg 92] ... [Msg 96]│
│  └─ 包含关键信息摘要                    │
└─────────────────────────────────────────┘
```

---

## 6. Session 状态管理

### 6.1 Session 生命周期

```typescript
enum SessionState {
  Initializing = "initializing",
  Active = "active",
  Compacting = "compacting",   // 正在压缩
  Archived = "archived",       // 已归档
  Error = "error",
}

interface Session {
  id: string
  state: SessionState
  version: string  // 用于乐观并发控制

  // 状态转换
  async initialize(): Promise<void>
  async compact(): Promise<void>
  async archive(): Promise<void>
  async restore(): Promise<void>  // 从归档恢复
}
```

### 6.2 分支与会话树

```typescript
// 支持会话分支，形成会话树
interface Session {
  id: string
  parent_id?: string        // 父会话
  children: Session[]       // 子会话
}

// 创建分支
async function createBranch(parentId: string, title: string): Promise<Session> {
  const parent = await loadSession(parentId)
  const branch = await createSession({
    parent_id: parentId,
    title,
    // 复制父会话的上下文
    initialContext: await getSessionContext(parentId),
  })
  return branch
}
```

---

## 7. 与 Agent Loop 的集成

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                     │
│  ┌─────────────────┐  ┌─────────────────────┐  ┌─────────────────┐  │
│  │ User Input      │──▶│ Session Manager     │──▶│ Message.create()│  │
│  └─────────────────┘  └─────────────────────┘  └─────────────────┘  │
│                                │                        │            │
│                                ▼                        ▼            │
│                       ┌─────────────────┐    ┌─────────────────┐     │
│                       │ loadContext()   │    │ Part.create()   │     │
│                       │ - 查询 Messages │    │ - TextPart      │     │
│                       │ - 查询 Parts    │    │ - SnapshotPart  │     │
│                       └─────────────────┘    │ - FilePart      │     │
│                                              └─────────────────┘     │
│                                │                                     │
│                                ▼                                     │
│                       ┌─────────────────┐                            │
│                       │ checkSize()     │                            │
│                       │ if > threshold: │                            │
│                       │   triggerCompaction()                       │
│                       └─────────────────┘                            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 8. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| 消息未保存 | 检查 Drizzle ORM 事务提交 | `session/session.sql.ts` |
| Part 类型错误 | 验证 Zod Schema | `session/message-v2.ts` |
| 上下文溢出 | 查看 `ContextOverflowError` 处理 | `session/message-v2.ts:48` |
| 会话分支失败 | 检查 `parent_id` 外键约束 | `session/session.sql.ts:18` |
| 压缩未触发 | 检查 `time_compacting` 时间戳 | `session/session.sql.ts:31` |

---

## 9. 架构特点总结

- **三层层级**: Session → Message → Part 清晰的层级关系
- **类型安全**: 使用 Zod + Drizzle 实现端到端类型安全
- **反范化设计**: Part 表冗余存储 session_id，优化查询性能
- **多样 Part 类型**: 支持 text / snapshot / patch / file / reasoning 等多种类型
- **Streaming 架构**: AsyncLocalStorage + Event Bus 实现响应式处理
- **乐观并发**: Session version 字段支持乐观锁
- **自动压缩**: 基于 Token 估算的自动上下文压缩
- **会话分支**: 支持从任意会话创建分支，形成会话树
