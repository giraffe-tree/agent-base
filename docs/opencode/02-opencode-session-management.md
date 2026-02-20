# Session 管理（opencode）

本文基于 `./opencode` 源码，解释 opencode 如何实现 session 生命周期管理、SQLite 持久化、消息-部件架构、context compaction、以及 ACP 协议集成。
为适配"先看全貌再看细节"的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 Session 生命周期流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 创建或恢复 Session                                       │
│  ┌─────────────────┐                                            │
│  │ Session.create()│ 或 │ Session.fromID()                     │
│  │ 生成降序 ID     │      │ 从数据库加载                        │
│  │ 生成 slug       │      │                                     │
│  └────────┬────────┘      │                                     │
└───────────┼───────────────┼─────────────────────────────────────┘
            │               │
            ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│  INITIALIZE: Session 初始化与数据库写入                          │
│  ┌────────────────────────────────────────┐                     │
│  │ createNext()                           │
│  │  ├── 生成唯一降序 ID (时间戳相关)       │
│  │  ├── 生成 URL-friendly slug             │
│  │  ├── 关联 project                      │
│  │  ├── 设置 parent/child 关系 (fork)     │
│  │  └── 写入 SessionTable (SQLite)        │
│  │                                      │
│  │ MessagePart 架构初始化                │
│  │  ├── MessageTable: 消息元数据          │
│  │  └── PartTable: 部件内容               │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  AGENT LOOP: Session 执行与状态管理                               │
│  ┌────────────────────────────────────────┐                     │
│  │ Session.loop()                         │
│  │  ├── 获取/恢复 session 状态            │
│  │  ├── 设置 SessionStatus = busy         │
│  │  ├── MessageV2.filterCompacted()       │
│  │  │   └── 过滤已压缩的消息              │
│  │  ├── 分析最后 user/assistant 消息      │
│  │  ├── check done? ───────────────────┐  │
│  │  │   └── Yes → break                │  │
│  │  ├── 处理 subtask ──────────────────┤  │
│  │  ├── 处理 compaction ───────────────┤  │
│  │  ├── check overflow ────────────────┤  │
│  │  │   └── Yes → trigger compaction   │  │
│  │  ├── resolveTools() ────────────────┤  │
│  │  └── processor.process() ───────────┘  │
│  │  ◄── 循环直到完成或中断                │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  FORK/REVERT: Session 分支与回退                                 │
│  ┌────────────────────────────────────────┐                     │
│  │ Session.fork()                         │
│  │  ├── clone session with new title      │
│  │  ├── copy messages up to messageID     │
│  │  └── remap IDs                         │
│  │                                      │
│  │ Session.revert()                       │
│  │  ├── snapshot-based restoration        │
│  │  └── cleanup reverted messages         │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ACP: Agent Communication Protocol 集成                          │
│  ┌────────────────────────────────────────┐                     │
│  │ ACPSessionManager                      │
│  │  ├── create()                          │
│  │  ├── load()                            │
│  │  └── 管理 session 状态映射             │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流
```

### 1.2 数据结构与存储关系图

```text
┌────────────────────────────────────────────────────────────────────┐
│ [A] 分层架构                                                        │
└────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────┐
    │                      ACP Layer                          │
    │              (Agent Communication Protocol)             │
    └─────────────────────────┬───────────────────────────────┘
                              │
    ┌─────────────────────────▼───────────────────────────────┐
    │                   Session Layer                         │
    │  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
    │  │   Session   │  │ SessionStatus│  │SessionCompaction│ │
    │  │   (CRUD)    │  │  (状态追踪)   │  │   (压缩管理)    │ │
    │  └──────┬──────┘  └──────────────┘  └────────────────┘ │
    └─────────┼───────────────────────────────────────────────┘
              │
    ┌─────────▼───────────────────────────────────────────────┐
    │                  Message Layer                          │
    │  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
    │  │  MessageV2  │  │  Part        │  │ MessageStream  │ │
    │  │  (消息容器)  │  │  (内容部件)   │  │   (流式加载)    │ │
    │  └──────┬──────┘  └──────────────┘  └────────────────┘ │
    └─────────┼───────────────────────────────────────────────┘
              │
    ┌─────────▼───────────────────────────────────────────────┐
    │                  Storage Layer                          │
    │              ┌─────────────────┐                        │
    │              │   SQLite +      │                        │
    │              │   Drizzle ORM   │                        │
    │              └─────────────────┘                        │
    └─────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ [B] 数据库 Schema                                                   │
└────────────────────────────────────────────────────────────────────┘

SessionTable
├── id: text (PK)
├── project_id: text (FK)
├── parent_id: text (self FK, for fork)
├── slug: text
├── directory: text
├── title: text
├── version: text
├── share_url: text
├── summary_additions: integer
├── summary_deletions: integer
├── summary_files: integer
├── summary_diffs: json
├── revert: json
├── permission: json
├── time_created: integer
├── time_updated: integer
├── time_compacting: integer
└── time_archived: integer

MessageTable                    PartTable
├── id: text (PK)               ├── id: text (PK)
├── session_id: text (FK)       ├── message_id: text (FK)
├── parent_id: text             ├── type: text
├── index: integer              ├── data: json
└── data: json                  └── status: text

┌────────────────────────────────────────────────────────────────────┐
│ [C] Part 类型系统                                                   │
└────────────────────────────────────────────────────────────────────┘

Part (discriminated union)
├── TextPart          # 文本内容
├── SubtaskPart       # 子任务
├── ReasoningPart     # 模型推理
├── FilePart          # 文件附件
├── ToolPart          # 工具调用与结果
├── StepStartPart     # 步骤开始
├── StepFinishPart    # 步骤结束
├── SnapshotPart      # 代码快照
├── PatchPart         # 代码补丁
├── AgentPart         # Agent 引用
├── RetryPart         # 重试标记
└── CompactionPart    # 压缩标记

图例: ───▶ 依赖/流向  ┌─┐ 模块/表
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 session 从创建到 ACP 集成的完整流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `3`（知道分层架构和数据库设计）。
- **10 分钟版**：通读 `3~7`（能定位 session、compaction、fork 相关问题）。

### 2.1 一句话定义

opencode 的 Session 是**"基于 SQLite 的多层状态管理单元"**：使用消息-部件分离架构存储对话，支持 fork/revert 操作，内置 compaction 管理上下文窗口，通过 ACP 协议对外提供服务。

---

## 3. 核心数据结构

### 3.1 Session Info

**文件**: `packages/opencode/src/session/index.ts`

```typescript
export const Info = z.object({
  id: Identifier.schema("session"),
  slug: z.string(),
  projectID: z.string(),
  directory: z.string(),
  parentID: Identifier.schema("session").optional(),  // fork 源
  summary: z.object({
    additions: z.number(),
    deletions: z.number(),
    files: z.number(),
    diffs: Snapshot.FileDiff.array().optional(),
  }).optional(),
  share: z.object({ url: z.string() }).optional(),
  title: z.string(),
  version: z.string(),
  time: z.object({
    created: z.number(),
    updated: z.number(),
    compacting: z.number().optional(),
    archived: z.number().optional(),
  }),
  permission: PermissionNext.Ruleset.optional(),
  revert: z.object({
    messageID: z.string(),
    partID: z.string().optional(),
    snapshot: z.string().optional(),
    diff: z.string().optional(),
  }).optional(),
})
```

### 3.2 Part 类型系统

**文件**: `packages/opencode/src/session/message-v2.ts`

```typescript
export const Part = z.discriminatedUnion("type", [
  TextPart,        // 用户或助手文本
  SubtaskPart,     // 子代理任务
  ReasoningPart,   // 模型推理
  FilePart,        // 文件附件
  ToolPart,        // 工具调用和结果
  StepStartPart,   // 步骤跟踪
  StepFinishPart,  // 步骤完成
  SnapshotPart,    // 代码快照
  PatchPart,       // 代码补丁
  AgentPart,       // 代理引用
  RetryPart,       // 重试尝试
  CompactionPart,  // 上下文压缩
])
```

---

## 4. Session 生命周期详解

### 4.1 创建 Session

**文件**: `packages/opencode/src/session/index.ts:192-306`

```typescript
export const create = fn(
  z.object({
    parentID: Identifier.schema("session").optional(),  // fork 支持
    title: z.string().optional(),
    permission: Info.shape.permission,
  }).optional(),
  async (input) => {
    return createNext({
      parentID: input?.parentID,
      directory: Instance.directory,
      title: input?.title,
      permission: input?.permission,
    })
  },
)

// createNext 实现 (lines 267-306)
async function createNext(input: {
  parentID?: string
  directory: string
  title?: string
  permission?: PermissionNext.Ruleset
}) {
  // 1. 生成降序 ID (时间戳相关)
  const id = generateDescendingId()
  // 2. 生成 slug
  const slug = generateSlug(input.title || "untitled")
  // 3. 关联 project
  const projectID = await getProjectID()
  // 4. 写入数据库
  await db.insert(SessionTable).values({
    id,
    slug,
    project_id: projectID,
    directory: input.directory,
    parent_id: input.parentID,
    title: input.title || "untitled",
    version: CURRENT_VERSION,
    permission: input.permission,
    time_created: Date.now(),
    time_updated: Date.now(),
  })
}
```

### 4.2 Fork Session

**文件**: `packages/opencode/src/session/index.ts:210-250`

```typescript
export const fork = fn(
  z.object({
    sessionID: Identifier.schema("session"),
    messageID: Identifier.schema("message").optional(),
  }),
  async (input) => {
    // 1. 克隆 session
    const newSession = await create({
      parentID: input.sessionID,
      title: `${original.title} (fork)`,
    })

    // 2. 复制消息到指定点
    const messages = await MessageV2.list(input.sessionID)
    const messagesToCopy = input.messageID
      ? messages.slice(0, findMessageIndex(messages, input.messageID))
      : messages

    // 3. 重新映射 ID 并克隆
    for (const msg of messagesToCopy) {
      const newMsg = await MessageV2.clone({
        sessionID: newSession.id,
        parentID: newParentID,
        data: msg.data,
      })
      // 克隆所有 parts
      for (const part of msg.parts) {
        await Part.clone({ messageID: newMsg.id, data: part })
      }
    }

    return newSession
  }
)
```

---

## 5. Agent Loop 详解

### 5.1 主循环

**文件**: `packages/opencode/src/session/prompt.ts:274-726`

```typescript
export const loop = fn(LoopInput, async (input) => {
  const { sessionID, resume_existing } = input
  const abort = resume_existing ? resume(sessionID) : start(sessionID)

  let step = 0
  const session = await Session.get(sessionID)

  while (true) {
    SessionStatus.set(sessionID, { type: "busy" })

    // 1. 获取并过滤消息 (排除已压缩)
    let msgs = await MessageV2.filterCompacted(MessageV2.stream(sessionID))

    // 2. 找到最后用户和助手消息
    let lastUser: MessageV2.User | undefined
    let lastAssistant: MessageV2.Assistant | undefined
    // ... 分析逻辑 ...

    // 3. 检查是否完成
    if (lastAssistant?.finish && !["tool-calls", "unknown"].includes(lastAssistant.finish)) {
      break
    }

    step++

    // 4. 处理子任务
    if (task?.type === "subtask") {
      // 执行子代理
      continue
    }

    // 5. 处理 compaction
    if (task?.type === "compaction") {
      const result = await SessionCompaction.process({...})
      continue
    }

    // 6. 检查上下文溢出
    if (await SessionCompaction.isOverflow({ tokens: lastFinished.tokens, model })) {
      await SessionCompaction.create({...})
      continue
    }

    // 7. 正常处理
    const processor = SessionProcessor.create({...})
    const tools = await resolveTools({...})
    const result = await processor.process({...})

    // 8. 处理完成或继续
    if (modelFinished) break
    if (result === "stop") break
    if (result === "compact") {
      // 触发 compaction
      continue
    }
  }
})
```

---

## 6. Compaction 机制

### 6.1 溢出检测

**文件**: `packages/opencode/src/session/compaction.ts`

```typescript
export async function isOverflow(input: {
  tokens: MessageV2.Assistant["tokens"]
  model: Provider.Model
}) {
  const config = await Config.get()
  if (config.compaction?.auto === false) return false

  const context = input.model.limit.context
  if (context === 0) return false

  const count = input.tokens.total ||
    input.tokens.input + input.tokens.output +
    input.tokens.cache.read + input.tokens.cache.write

  const reserved = config.compaction?.reserved ??
    Math.min(COMPACTION_BUFFER, ProviderTransform.maxOutputTokens(input.model))

  const usable = input.model.limit.input
    ? input.model.limit.input - reserved
    : context - ProviderTransform.maxOutputTokens(input.model)

  return count >= usable
}
```

### 6.2 压缩策略

```typescript
// 压缩策略 (compaction.ts:58-99)
// 1. 保护最近 40K token 的工具调用
// 2. 移除旧工具调用输出
// 3. 保留 skill 工具输出
// 4. 压缩超过 2 轮的消息
```

---

## 7. 状态管理

### 7.1 SessionStatus

**文件**: `packages/opencode/src/session/status.ts`

```typescript
export namespace SessionStatus {
  export const Info = z.union([
    z.object({ type: z.literal("idle") }),
    z.object({
      type: z.literal("retry"),
      attempt: z.number(),
      message: z.string(),
      next: z.number(),
    }),
    z.object({ type: z.literal("busy") }),
  ])

  const state = Instance.state(() => {
    const data: Record<string, Info> = {}
    return data
  })

  export function set(sessionID: string, status: Info) {
    Bus.publish(Event.Status, { sessionID, status })
    if (status.type === "idle") {
      delete state()[sessionID]
      return
    }
    state()[sessionID] = status
  }
}
```

---

## 8. ACP 集成

### 8.1 ACPSessionManager

**文件**: `packages/opencode/src/acp/session.ts`

```typescript
export class ACPSessionManager {
  private sessions = new Map<string, ACPSessionState>()

  async create(
    cwd: string,
    mcpServers: McpServer[],
    model?: ACPSessionState["model"]
  ): Promise<ACPSessionState> {
    const session = await this.sdk.session.create(
      { directory: cwd },
      { throwOnError: true }
    )
    const sessionId = session.id

    const state: ACPSessionState = {
      id: sessionId,
      cwd,
      mcpServers,
      createdAt: new Date(),
      model: resolvedModel,
    }
    this.sessions.set(sessionId, state)
    return state
  }

  async load(sessionId: string, cwd: string, mcpServers: McpServer[]): Promise<ACPSessionState> {
    // 从 SDK 加载现有 session
  }
}
```

### 8.2 ACP Agent

**文件**: `packages/opencode/src/acp/agent.ts`

处理：
- Session 生命周期（create, load, fork）
- 权限请求
- 工具调用执行
- 事件流
- 使用量追踪

---

## 9. 全局状态管理

### 9.1 State 系统

**文件**: `packages/opencode/src/project/state.ts`

```typescript
export namespace State {
  const recordsByKey = new Map<string, Map<any, Entry>>()

  export function create<S>(
    root: () => string,
    init: () => S,
    dispose?: (state: Awaited<S>) => Promise<void>
  ) {
    return () => {
      const key = root()
      let entries = recordsByKey.get(key)
      if (!entries) {
        entries = new Map<string, Entry>()
        recordsByKey.set(key, entries)
      }
      const exists = entries.get(init)
      if (exists) return exists.state as S

      const state = init()
      entries.set(init, { state, dispose })
      return state
    }
  }
}
```

---

## 10. 排障速查

| 问题 | 检查点 |
|------|--------|
| session 创建失败 | 检查 SQLite 数据库权限和 schema 版本 |
| fork 失败 | 查看 `Session.fork` 的 ID 重新映射逻辑 |
| compaction 未触发 | 检查 `isOverflow` 的阈值计算 |
| 消息丢失 | 查看 `filterCompacted` 是否过滤过度 |
| ACP 连接失败 | 检查 `ACPSessionManager` 的状态映射 |
| 状态不一致 | 查看 `SessionStatus` 的事件发布 |

---

## 11. 架构特点总结

1. **分层架构**: Storage → Message → Session → ACP 四层分离
2. **关系型存储**: SQLite + Drizzle ORM，支持复杂查询
3. **消息-部件分离**: Message 存元数据，Part 存内容，支持富媒体
4. **Fork/Revert**: 完整支持分支和回退操作
5. **自动压缩**: 智能上下文管理，保护关键信息
6. **事件驱动**: Bus 系统支持松耦合通信
7. **多代理**: Subtask 支持代理委派
8. **权限系统**: 细粒度访问控制
