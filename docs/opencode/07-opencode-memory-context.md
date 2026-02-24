# Memory Context 管理（opencode）

## TL;DR（结论先行）

一句话定义：OpenCode 的 Memory Context 采用"**三层层级 + SQLite 持久化 + Event Bus 响应式架构**"的设计，通过 Session → Message → Part 的三级结构组织对话数据，使用 SQLite + Drizzle ORM 进行类型安全的持久化，并基于 Event Bus 实现响应式的流式处理。

OpenCode 的核心取舍：**关系型数据库 + 反范化设计 + Prune 压缩**（对比 Kimi CLI 的 JSONL + Checkpoint、Gemini CLI 的分层 GEMINI.md、Codex 的惰性压缩）

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

没有 Memory Context 管理：
```
用户提问 → LLM 缺乏对话历史 → 每次都要重复背景 → 效率低下
长对话 → Token 超限 → 请求失败 → 对话中断
```

有 Memory Context 管理：
```
用户提问 → 自动加载 Session 历史 → LLM 了解上下文 → 精准回答
长对话 → 自动触发 Compaction → 压缩历史 → 对话继续
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 对话持久化 | 程序重启后丢失所有上下文 |
| Token 超限 | 长对话导致上下文窗口溢出 |
| 类型安全 | 数据结构不一致导致运行时错误 |
| 响应式更新 | UI 无法实时同步消息变化 |
| 会话分支 | 无法从任意点创建新的对话分支 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Processor                               │
│ packages/opencode/src/session/processor.ts                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ MessageV2    │ │ Compaction   │ │ Session      │
│ 消息管理     │ │ 上下文压缩   │ │ 会话管理     │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Memory Context ▓▓▓                                      │
│ packages/opencode/src/session/                               │
│ - session.sql.ts      : Session/Message/Part 表定义         │
│ - message-v2.ts       : Part 类型系统 + 流式读取            │
│ - index.ts            : Session CRUD 操作                   │
│ - compaction.ts       : 自动压缩实现                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Drizzle ORM  │ │ Event Bus    │ │ AsyncLocal   │
│ SQLite       │ │ 事件总线     │ │ Storage      │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `SessionTable` | 会话表定义，支持分支结构 | `packages/opencode/src/session/session.sql.ts:11` |
| `MessageTable` | 消息表定义，存储角色和元数据 | `packages/opencode/src/session/session.sql.ts:37` |
| `PartTable` | 内容片段表，反范化存储 session_id | `packages/opencode/src/session/session.sql.ts:50` |
| `MessageV2.Part` | Part 类型系统（Zod Schema） | `packages/opencode/src/session/message-v2.ts:76` |
| `MessageV2.stream` | 流式读取消息 | `packages/opencode/src/session/message-v2.ts:716` |
| `Session.create` | 创建会话 | `packages/opencode/src/session/index.ts:212` |
| `Session.fork` | 创建会话分支 | `packages/opencode/src/session/index.ts:230` |
| `SessionCompaction` | 上下文压缩 | `packages/opencode/src/session/compaction.ts:18` |
| `Bus.publish` | 发布事件 | `packages/opencode/src/bus/index.ts:41` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant S as Session
    participant M as MessageV2
    participant D as Database
    participant B as Bus

    A->>S: createSession()
    Note over S: 创建新会话
    S->>D: insert(SessionTable)
    S->>B: publish(Event.Created)

    A->>M: toModelMessages()
    Note over M: 转换消息格式
    M->>D: select(MessageTable)
    M->>D: select(PartTable)

    A->>S: updatePart()
    S->>D: insert(PartTable)
    S->>B: publish(PartUpdated)
    Note over B: 通知 UI 更新
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent Loop 创建会话 | 解耦业务逻辑与存储 |
| 2 | 写入 SQLite | 持久化会话数据 |
| 3 | 发布创建事件 | 响应式通知 UI 更新 |
| 4 | 转换消息格式 | 适配不同 Provider 的模型消息格式 |
| 5-6 | 查询消息和片段 | 分层读取，优化性能 |
| 7-9 | 更新 Part 并发布事件 | 实时同步到 UI |

---

## 3. 核心组件详细分析

### 3.1 数据库 Schema 设计

#### 职责定位

数据库层负责 Session、Message、Part 三层层级结构的持久化，使用 Drizzle ORM 实现类型安全的数据库操作。

#### 表结构关系

```text
┌─────────────────────────────────────────────────────────────┐
│  Session 表                                                  │
│  ├── id (PK)                                                │
│  ├── project_id (FK)                                        │
│  ├── parent_id (自引用，支持分支)                            │
│  ├── title, version, directory                              │
│  ├── summary_additions/deletions/files (统计信息)            │
│  ├── revert (回滚信息 JSON)                                  │
│  ├── permission (权限规则 JSON)                              │
│  └── time_created/updated/compacting/archived               │
└───────────────────────┬─────────────────────────────────────┘
                        │ 1:N
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Message 表                                                  │
│  ├── id (PK)                                                │
│  ├── session_id (FK)                                        │
│  ├── data (JSON: role, time, model, etc.)                   │
│  └── time_created                                           │
└───────────────────────┬─────────────────────────────────────┘
                        │ 1:N
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Part 表                                                     │
│  ├── id (PK)                                                │
│  ├── message_id (FK)                                        │
│  ├── session_id (反范化，便于查询)                           │
│  ├── data (JSON: type-specific content)                     │
│  └── time_created                                           │
└─────────────────────────────────────────────────────────────┘
```

#### 关键代码

```typescript
// packages/opencode/src/session/session.sql.ts:11-35
export const SessionTable = sqliteTable(
  "session",
  {
    id: text().primaryKey(),
    project_id: text()
      .notNull()
      .references(() => ProjectTable.id, { onDelete: "cascade" }),
    parent_id: text(),  // 支持会话分支
    // ... 其他字段
    revert: text({ mode: "json" }).$type<{ messageID: string; partID?: string; snapshot?: string; diff?: string }>(),
    permission: text({ mode: "json" }).$type<PermissionNext.Ruleset>(),
    time_compacting: integer(),
    time_archived: integer(),
  },
  (table) => [index("session_project_idx").on(table.project_id), index("session_parent_idx").on(table.parent_id)],
)
```

**设计要点**：
1. **反范化设计**：Part 表冗余存储 `session_id`，避免 JOIN 查询
2. **JSON 字段**：灵活存储结构化数据（revert、permission、diffs）
3. **索引优化**：project_id、parent_id、session_id 均有索引

---

### 3.2 Part 类型系统

#### 职责定位

Part 是 OpenCode 的最小内容单元，支持多种类型（text、tool、reasoning、file 等），使用 Zod 实现运行时类型安全。

#### 类型层次结构

```mermaid
flowchart TD
    PartBase["PartBase<br/>id, sessionID, messageID"] --> TextPart["TextPart<br/>text, synthetic, ignored"]
    PartBase --> SnapshotPart["SnapshotPart<br/>snapshot ID"]
    PartBase --> PatchPart["PatchPart<br/>hash, files[]"]
    PartBase --> FilePart["FilePart<br/>mime, filename, url, source"]
    PartBase --> ReasoningPart["ReasoningPart<br/>text, metadata"]
    PartBase --> ToolPart["ToolPart<br/>tool, callID, state"]
    PartBase --> StepStartPart["StepStartPart<br/>snapshot"]
    PartBase --> StepFinishPart["StepFinishPart<br/>reason, cost, tokens"]
    PartBase --> CompactionPart["CompactionPart<br/>auto"]
    PartBase --> SubtaskPart["SubtaskPart<br/>prompt, description, agent"]
    PartBase --> RetryPart["RetryPart<br/>attempt, error"]
    PartBase --> AgentPart["AgentPart<br/>name, source"]

    style ToolPart fill:#90EE90
    style TextPart fill:#87CEEB
    style ReasoningPart fill:#FFB6C1
```

#### Tool State 状态机

```mermaid
stateDiagram-v2
    [*] --> Pending: 创建工具调用
    Pending --> Running: 开始执行
    Running --> Completed: 执行成功
    Running --> Error: 执行失败
    Completed --> [*]
    Error --> [*]

    Pending: status="pending"
    Pending: input, raw

    Running: status="running"
    Running: input, title, time.start

    Completed: status="completed"
    Completed: input, output, title
    Completed: time.start, time.end
    Completed: time.compacted (可选)

    Error: status="error"
    Error: input, error
    Error: time.start, time.end
```

#### 关键代码

```typescript
// packages/opencode/src/session/message-v2.ts:76-114
const PartBase = z.object({
  id: z.string(),
  sessionID: z.string(),
  messageID: z.string(),
})

export const TextPart = PartBase.extend({
  type: z.literal("text"),
  text: z.string(),
  synthetic: z.boolean().optional(),  // 是否由系统生成
  ignored: z.boolean().optional(),    // 是否被忽略
  time: z.object({ start: z.number(), end: z.number().optional() }),
})

// Tool State 联合类型
export const ToolState = z.discriminatedUnion("status", [
  ToolStatePending,   // pending
  ToolStateRunning,   // running
  ToolStateCompleted, // completed
  ToolStateError,     // error
])
```

---

### 3.3 组件间协作时序

展示 Session、Message、Part 如何协作完成一次完整的消息创建流程。

```mermaid
sequenceDiagram
    participant U as User/Agent
    participant S as Session
    participant M as MessageV2
    participant D as Database
    participant B as Bus

    U->>S: updateMessage(msgInfo)
    activate S

    S->>D: insert(MessageTable)
    Note right of S: ON CONFLICT DO UPDATE
    S->>B: publish(MessageV2.Event.Updated)

    S-->>U: return msg
    deactivate S

    U->>S: updatePart(part)
    activate S

    S->>D: insert(PartTable)
    Note right of S: ON CONFLICT DO UPDATE
    S->>B: publish(MessageV2.Event.PartUpdated)

    S-->>U: return part
    deactivate S

    U->>M: stream(sessionID)
    activate M

    M->>D: select(MessageTable) ORDER BY time_created DESC
    M->>D: select(PartTable) WHERE message_id IN (...)

    loop 分页读取
        M-->>U: yield { info, parts }
    end

    deactivate M
```

**协作要点**：

1. **消息创建**：使用 `INSERT ... ON CONFLICT DO UPDATE` 实现 upsert 语义
2. **事件通知**：每次更新后立即发布事件，确保 UI 实时同步
3. **流式读取**：使用 Generator 函数实现分页加载，避免内存溢出

---

### 3.4 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[用户输入/AI输出] --> I2[创建 Message]
        I2 --> I3[创建 Part]
    end

    subgraph Process["处理阶段"]
        P1[持久化到 SQLite] --> P2[发布更新事件]
        P2 --> P3[UI 响应式更新]
    end

    subgraph Output["输出阶段"]
        O1[toModelMessages] --> O2[Provider 格式转换]
        O2 --> O3[发送到 LLM]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 压缩路径（Token 超限）

```mermaid
flowchart TD
    Start[Token 检查] --> Check{count >= usable?}
    Check -->|是| Compact[触发 Compaction]
    Check -->|否| Continue[继续对话]

    Compact --> Prune[Prune 旧工具结果]
    Prune --> Summary[生成 Summary]
    Summary --> CompactionPart[插入 CompactionPart]

    CompactionPart --> Continue

    style Compact fill:#FFD700
    style Prune fill:#90EE90
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant U as User
    participant S as Session
    participant M as MessageV2
    participant D as Database
    participant B as Bus
    participant L as LLM API

    U->>S: 发送消息
    S->>S: updateMessage(userMsg)
    S->>D: INSERT MessageTable
    S->>B: publish(Message.Updated)

    S->>S: updatePart(textPart)
    S->>D: INSERT PartTable
    S->>B: publish(PartUpdated)

    M->>M: toModelMessages(sessionID)
    M->>D: SELECT messages + parts
    M-->>L: 发送格式化消息

    L-->>M: 流式响应
    loop 每收到一个 chunk
        M->>S: updatePartDelta()
        S->>B: publish(PartDelta)
    end

    M->>S: updateMessage(assistantMsg)
    S->>D: INSERT MessageTable
    S->>B: publish(Message.Updated)
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | 用户输入 | 创建 User Message | MessageV2.User | `packages/opencode/src/session/index.ts:670` |
| 处理 | 历史消息 | toModelMessages 转换 | ModelMessage[] | `packages/opencode/src/session/message-v2.ts:491` |
| 流式 | LLM 响应 | 增量更新 Part | PartDelta 事件 | `packages/opencode/src/session/index.ts:758` |
| 完成 | Assistant 消息 | 创建 Assistant Message | MessageV2.Assistant | `packages/opencode/src/session/index.ts:670` |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入层"]
        I1[User Input] --> I2[Session.updateMessage]
        I3[AI Response] --> I4[Session.updatePart]
    end

    subgraph Storage["存储层"]
        S1[(SessionTable)]
        S2[(MessageTable)]
        S3[(PartTable)]
    end

    subgraph Event["事件层"]
        E1[Message.Updated]
        E2[PartUpdated]
        E3[PartDelta]
    end

    subgraph Output["输出层"]
        O1[toModelMessages]
        O2[UI 渲染]
    end

    I2 --> S2
    I4 --> S3
    S2 --> E1
    S3 --> E2
    E2 --> O2
    S2 --> O1
    S3 --> O1

    style Storage fill:#f9f,stroke:#333
    style Event fill:#bfb,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始处理] --> B{Token 检查}
    B -->|正常| C[继续对话]
    B -->|超限| D[触发 Compaction]

    D --> E{Prune 策略}
    E -->|清除旧工具结果| F[标记 time.compacted]
    E -->|生成 Summary| G[插入 CompactionPart]

    F --> H{是否足够?}
    G --> H
    H -->|是| C
    H -->|否| I[返回错误]

    C --> J[结束]
    I --> J

    style D fill:#FFD700
    style I fill:#FF6B6B
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```typescript
// packages/opencode/src/session/session.sql.ts:11-35
export const SessionTable = sqliteTable(
  "session",
  {
    id: text().primaryKey(),
    project_id: text().notNull().references(() => ProjectTable.id, { onDelete: "cascade" }),
    parent_id: text(),  // 支持分支
    slug: text().notNull(),
    directory: text().notNull(),
    title: text().notNull(),
    version: text().notNull(),
    summary_additions: integer(),
    summary_deletions: integer(),
    summary_files: integer(),
    summary_diffs: text({ mode: "json" }).$type<Snapshot.FileDiff[]>(),
    revert: text({ mode: "json" }).$type<{ messageID: string; partID?: string; snapshot?: string; diff?: string }>(),
    permission: text({ mode: "json" }).$type<PermissionNext.Ruleset>(),
    ...Timestamps,
    time_compacting: integer(),
    time_archived: integer(),
  },
  (table) => [
    index("session_project_idx").on(table.project_id),
    index("session_parent_idx").on(table.parent_id),
  ],
)
```

**字段说明**：
| 字段 | 类型 | 用途 |
|-----|------|------|
| `parent_id` | `string?` | 支持会话分支，指向父会话 |
| `revert` | `JSON` | 存储回滚点信息（messageID, snapshot, diff） |
| `permission` | `JSON` | 存储权限规则 |
| `time_compacting` | `integer` | 上次压缩时间戳 |
| `summary_*` | `integer` | 代码变更统计 |

### 5.2 流式消息读取

```typescript
// packages/opencode/src/session/message-v2.ts:716-767
export const stream = fn(Identifier.schema("session"), async function* (sessionID) {
  const size = 50
  let offset = 0
  while (true) {
    const rows = Database.use((db) =>
      db
        .select()
        .from(MessageTable)
        .where(eq(MessageTable.session_id, sessionID))
        .orderBy(desc(MessageTable.time_created))
        .limit(size)
        .offset(offset)
        .all(),
    )
    if (rows.length === 0) break

    const ids = rows.map((row) => row.id)
    const partsByMessage = new Map<string, MessageV2.Part[]>()
    if (ids.length > 0) {
      const partRows = Database.use((db) =>
        db
          .select()
          .from(PartTable)
          .where(inArray(PartTable.message_id, ids))
          .orderBy(PartTable.message_id, PartTable.id)
          .all(),
      )
      for (const row of partRows) {
        const part = { ...row.data, id: row.id, sessionID: row.session_id, messageID: row.message_id } as MessageV2.Part
        const list = partsByMessage.get(row.message_id)
        if (list) list.push(part)
        else partsByMessage.set(row.message_id, [part])
      }
    }

    for (const row of rows) {
      const info = { ...row.data, id: row.id, sessionID: row.session_id } as MessageV2.Info
      yield { info, parts: partsByMessage.get(row.id) ?? [] }
    }

    offset += rows.length
    if (rows.length < size) break
  }
})
```

**代码要点**：
1. **分页读取**：每页 50 条消息，避免内存溢出
2. **批量查询 Part**：先收集所有 message_id，再一次性查询 Parts
3. **Generator 函数**：使用 `async function*` 实现流式输出

### 5.3 关键调用链

```text
Session.updateMessage()     [packages/opencode/src/session/index.ts:670]
  -> Database.use()         [packages/opencode/src/storage/db.ts]
    -> db.insert(MessageTable)
    -> Database.effect()    [packages/opencode/src/storage/db.ts]
      -> Bus.publish(MessageV2.Event.Updated)

Session.updatePart()        [packages/opencode/src/session/index.ts:735]
  -> Database.use()
    -> db.insert(PartTable).onConflictDoUpdate()
    -> Database.effect()
      -> Bus.publish(MessageV2.Event.PartUpdated)

MessageV2.stream()          [packages/opencode/src/session/message-v2.ts:716]
  -> Database.use()
    -> select(MessageTable) ORDER BY time_created DESC
    -> select(PartTable) WHERE message_id IN (...)
  -> yield { info, parts }
```

---

## 6. 设计意图与 Trade-off

### 6.1 OpenCode 的选择

| 维度 | OpenCode 的选择 | 替代方案 | 取舍分析 |
|-----|----------------|---------|---------|
| 存储引擎 | SQLite + Drizzle ORM | JSONL (Kimi)、内存 (Codex) | 类型安全、关系查询能力强，但依赖文件系统 |
| 数据建模 | 三层层级 (Session→Message→Part) | 扁平结构 | 灵活支持多种内容类型，但查询需要 JOIN |
| 反范化 | Part 表冗余 session_id | 完全范化 | 优化查询性能，但增加存储冗余 |
| 压缩策略 | Prune + Compaction | 截断 (Codex)、摘要 (Kimi) | 保留关键信息，但实现复杂 |
| 响应式 | Event Bus | 轮询、回调 | 实时同步，但增加系统复杂度 |

### 6.2 为什么这样设计？

**核心问题**：如何在保证类型安全的同时，支持灵活的对话内容建模和实时 UI 同步？

**OpenCode 的解决方案**：
- 代码依据：`packages/opencode/src/session/session.sql.ts:11`
- 设计意图：使用关系型数据库 + ORM 实现类型安全的持久化，通过反范化优化查询性能
- 带来的好处：
  - 类型安全：Drizzle ORM 提供端到端类型安全
  - 灵活建模：Part 类型系统支持 12 种内容类型
  - 实时同步：Event Bus 实现响应式更新
  - 查询优化：反范化设计减少 JOIN 操作
- 付出的代价：
  - 复杂度：需要维护 Schema 和类型定义
  - 存储：反范化增加存储空间
  - 依赖：需要 SQLite 运行时

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统方案"
    branch "opencode"
    checkout opencode
    commit id: "SQLite + 三层层级"
    checkout main
    branch "kimi-cli"
    checkout kimi-cli
    commit id: "JSONL + Checkpoint"
    checkout main
    branch "gemini-cli"
    checkout gemini-cli
    commit id: "分层 GEMINI.md"
    checkout main
    branch "codex"
    checkout codex
    commit id: "惰性压缩"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| OpenCode | SQLite + 三层层级 + Event Bus | 需要强类型和实时同步的桌面应用 |
| Kimi CLI | JSONL + Checkpoint 回滚 | 需要状态回滚的 CLI 工具 |
| Gemini CLI | 分层 GEMINI.md + JIT 加载 | 需要项目级记忆发现的场景 |
| Codex | 惰性压缩 + 沙箱 | 企业级安全要求 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| Token 超限 | `count >= usable` | `packages/opencode/src/session/compaction.ts:32` |
| 会话归档 | `time_archived` 被设置 | `packages/opencode/src/session/index.ts:390` |
| 消息错误 | `message.error` 存在 | `packages/opencode/src/session/message-v2.ts:398` |

### 7.2 超时/资源限制

```typescript
// packages/opencode/src/session/compaction.ts:30-48
const COMPACTION_BUFFER = 20_000
const PRUNE_MINIMUM = 20_000
const PRUNE_PROTECT = 40_000

export async function isOverflow(input: { tokens: MessageV2.Assistant["tokens"]; model: Provider.Model }) {
  const config = await Config.get()
  if (config.compaction?.auto === false) return false
  const context = input.model.limit.context
  if (context === 0) return false

  const count = input.tokens.total || input.tokens.input + input.tokens.output + input.tokens.cache.read + input.tokens.cache.write

  const reserved = config.compaction?.reserved ?? Math.min(COMPACTION_BUFFER, ProviderTransform.maxOutputTokens(input.model))
  const usable = input.model.limit.input
    ? input.model.limit.input - reserved
    : context - ProviderTransform.maxOutputTokens(input.model)
  return count >= usable
}
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| ContextOverflow | 触发 Compaction | `packages/opencode/src/session/compaction.ts:32` |
| APIError | 重试或标记失败 | `packages/opencode/src/session/message-v2.ts:811` |
| AuthError | 提示用户重新认证 | `packages/opencode/src/session/message-v2.ts:822` |
| 连接重置 | 标记为可重试 | `packages/opencode/src/session/message-v2.ts:830` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| Session 表定义 | `packages/opencode/src/session/session.sql.ts` | 11-35 | SessionTable Schema |
| Message 表定义 | `packages/opencode/src/session/session.sql.ts` | 37-48 | MessageTable Schema |
| Part 表定义 | `packages/opencode/src/session/session.sql.ts` | 50-62 | PartTable Schema |
| Part 类型系统 | `packages/opencode/src/session/message-v2.ts` | 76-389 | Zod Schema 定义 |
| 流式读取 | `packages/opencode/src/session/message-v2.ts` | 716-767 | stream() Generator |
| 消息转换 | `packages/opencode/src/session/message-v2.ts` | 491-714 | toModelMessages() |
| 创建会话 | `packages/opencode/src/session/index.ts` | 212-228 | Session.create() |
| Fork 会话 | `packages/opencode/src/session/index.ts` | 230-270 | Session.fork() |
| 更新消息 | `packages/opencode/src/session/index.ts` | 670-690 | Session.updateMessage() |
| 更新 Part | `packages/opencode/src/session/index.ts` | 735-756 | Session.updatePart() |
| 压缩检查 | `packages/opencode/src/session/compaction.ts` | 32-48 | isOverflow() |
| Prune 实现 | `packages/opencode/src/session/compaction.ts` | 58-99 | prune() |
| 压缩处理 | `packages/opencode/src/session/compaction.ts` | 101-229 | process() |
| 事件发布 | `packages/opencode/src/bus/index.ts` | 41-64 | Bus.publish() |
| 事件定义 | `packages/opencode/src/bus/bus-event.ts` | 12-19 | BusEvent.define() |
| AsyncLocalStorage | `packages/opencode/src/util/context.ts` | 10-25 | Context.create() |

---

## 9. 延伸阅读

- 前置知识：`docs/comm/07-comm-memory-context.md`
- 相关机制：`docs/opencode/04-opencode-agent-loop.md`
- 深度分析：`docs/opencode/questions/opencode-compaction-strategy.md`（待创建）

---

*✅ Verified: 基于 opencode/packages/opencode/src/session/*.ts 源码分析*
*基于版本：2026-02-08 | 最后更新：2026-02-24*
