# opencode 概述文档

## 1. 项目简介

**opencode** 是一个多模型支持的 TypeScript/Bun CLI Agent，提供丰富的交互体验和可扩展的工具系统。

### 项目定位和目标
- 多模型支持的通用 CLI Agent
- 支持 OpenAI、Anthropic、Google、本地模型等多种提供商
- 提供 TUI（终端用户界面）和 Web 两种交互模式
- 强调可扩展性，支持插件系统和 MCP 协议
- 基于 Git 的快照机制实现状态管理

### 技术栈
- **语言**: TypeScript
- **运行时**: Bun
- **核心依赖**:
  - `yargs` - CLI 框架
  - `ai` - Vercel AI SDK（多模型统一接口）
  - `@libsql/client` - SQLite 数据库
  - `zod` - 数据验证
  - `ink` - React TUI 渲染

### 官方仓库
- https://github.com/opencode-ai/opencode

---

## 2. 架构概览

### 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI Layer                              │
│  (opencode/packages/opencode/src/index.ts:1)               │
│  ├─ yargs: 命令解析                                         │
│  ├─ 全局中间件: 日志初始化、数据库迁移                        │
│  └─ 子命令: run, tui, agent, models, etc                    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Commands Layer                            │
│  (opencode/packages/opencode/src/cli/cmd/)                 │
│  ├─ run.ts - 运行任务                                       │
│  ├─ tui/ - TUI 相关命令                                     │
│  ├─ agent.ts - Agent 管理                                   │
│  └─ ...                                                     │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                 Session Layer                               │
│  (opencode/packages/opencode/src/session/)                 │
│  ├─ session.ts - 会话管理                                   │
│  ├─ processor.ts:26 - SessionProcessor                      │
│  ├─ message-v2.ts - 消息类型                                │
│  └─ llm.ts - 模型调用                                       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Agent Layer                              │
│  (opencode/packages/opencode/src/agent/)                   │
│  ├─ agent.ts - Agent 定义                                   │
│  ├─ runtime.ts - 运行时                                     │
│  └─ loop.ts - 循环控制                                      │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Tools Layer                              │
│  (opencode/packages/opencode/src/tool/)                    │
│  ├─ tool.ts - 工具定义                                      │
│  ├─ registry.ts - 工具注册表                                │
│  └─ handlers/ - 工具实现                                    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  Snapshot Layer                             │
│  (opencode/packages/opencode/src/snapshot/index.ts)        │
│  ├─ Git 快照管理                                            │
│  ├─ 状态持久化                                              │
│  └─ 会话恢复                                                │
└─────────────────────────────────────────────────────────────┘
```

### 各层职责说明

| 层级 | 文件路径 | 核心职责 |
|------|----------|----------|
| CLI | `src/index.ts` | 入口、命令解析、全局初始化 |
| Commands | `src/cli/cmd/` | 子命令实现 |
| Session | `src/session/` | 会话管理、消息处理、流式响应 |
| Agent | `src/agent/` | Agent 定义、运行时、循环控制 |
| Tools | `src/tool/` | 工具注册、调度、执行 |
| Snapshot | `src/snapshot/` | Git 快照、状态管理 |

### 核心组件列表

1. **SessionProcessor** (session/processor.ts:26) - 会话处理器
2. **Agent** (agent/agent.ts) - Agent 定义
3. **ToolRegistry** (tool/registry.ts) - 工具注册表
4. **Snapshot** (snapshot/index.ts) - Git 快照
5. **LLM** (session/llm.ts) - 模型调用封装
6. **Bus** (bus.ts) - 事件总线

---

## 3. 入口与 CLI

### 入口文件路径
```
opencode/packages/opencode/src/index.ts:1
```

### CLI 参数解析方式

使用 `yargs` 库进行命令解析：

```typescript
// src/index.ts:47
const cli = yargs(hideBin(process.argv))
  .parserConfiguration({ "populate--": true })
  .scriptName("opencode")
  .wrap(100)
  .help("help", "show help")
  .alias("help", "h")
  .version("version", "show version number", Installation.VERSION)
  .alias("version", "v")
  .option("print-logs", {
    describe: "print logs to stderr",
    type: "boolean",
  })
  .option("log-level", {
    describe: "log level",
    type: "string",
    choices: ["DEBUG", "INFO", "WARN", "ERROR"],
  })
  .middleware(async (opts) => {
    // 全局中间件: 初始化日志、数据库迁移
    await Log.init({ ... });
    await JsonMigration.run(Database.Client().$client, ...);
  })
  .command(AcpCommand)
  .command(McpCommand)
  .command(TuiThreadCommand)
  .command(RunCommand)
  // ... 更多命令
```

### 启动流程

```
src/index.ts:1
       │
       ├─ 注册全局错误处理
       │   ├─ unhandledRejection
       │   └─ uncaughtException
       │
       ▼
yargs 解析
       │
       ├─ 执行全局中间件
       │   ├─ Log.init()
       │   └─ 数据库迁移
       │
       ├─ 匹配子命令
       │   ├─ "run" ──▶ RunCommand
       │   ├─ "tui" ──▶ TuiThreadCommand
       │   ├─ "agent" ──▶ AgentCommand
       │   └─ ...
       │
       └─ 无子命令 ──▶ 显示帮助/Logo
```

---

## 4. Agent 循环机制

### 主循环代码位置

```
opencode/packages/opencode/src/session/processor.ts:26
opencode/packages/opencode/src/agent/loop.ts
```

### 流程图（文本形式）

```
┌─────────────────┐
│ RunCommand /    │
│ TuiThreadCommand│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Session.create  │
│ 创建会话        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Agent.create    │
│ 创建 Agent      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│       主循环                         │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 等待用户输入 / 任务            │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ SessionProcessor.create()     │  │  ──▶ processor.ts:26
│  │                               │  │
│  │ 1. 创建 assistantMessage      │  │
│  │ 2. LLM.stream() 发起流式请求  │  │
│  │ 3. process() 处理流           │  │  ──▶ processor.ts:45
│  │                               │  │
│  └───────────────────────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ process() 循环                │  │
│  │                               │  │
│  │ while (true) {                │  │
│  │   ┌───────────────────────┐   │  │
│  │   │ 读取流事件             │   │  │
│  │   │ switch (value.type) { │   │  │
│  │   │   case "text-delta":  │   │  │
│  │   │     显示文本           │   │  │
│  │   │   case "tool-call":   │   │  │
│  │   │     执行工具           │   │  │
│  │   │   case "tool-result": │   │  │
│  │   │     发送结果到模型     │   │  │
│  │   │   case "finish-step": │   │  │
│  │   │     创建 Snapshot      │   │  │
│  │   │ }                      │   │  │
│  │   └───────────────────────┘   │  │
│  │ }                             │  │
│  │                               │  │
│  └───────────────────────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ 保存 Session                  │  │
│  │ 等待下一轮输入                │  │
│  └───────────────────────────────┘  │
│                                     │
└─────────────────────────────────────┘
```

### 单次循环的执行步骤

**SessionProcessor.create()** (processor.ts:26):

```typescript
export function create(input: {
  assistantMessage: MessageV2.Assistant;
  sessionID: string;
  model: Provider.Model;
  abort: AbortSignal;
}) {
  const toolcalls: Record<string, MessageV2.ToolPart> = {};
  let snapshot: string | undefined;

  return {
    async process(streamInput: LLM.StreamInput) {
      const stream = await LLM.stream(streamInput);

      for await (const value of stream.fullStream) {
        input.abort.throwIfAborted();

        switch (value.type) {
          case "start":
            SessionStatus.set(input.sessionID, { type: "busy" });
            break;

          case "text-delta":
            // 累积文本
            await Session.updatePartDelta({
              sessionID: input.sessionID,
              messageID: input.assistantMessage.id,
              partID: part.id,
              field: "text",
              delta: value.text,
            });
            break;

          case "tool-call": {
            // 创建工具调用部分
            const part = await Session.updatePart({
              type: "tool",
              tool: value.toolName,
              callID: value.id,
              state: { status: "running", input: value.input },
            });
            toolcalls[value.toolCallId] = part as MessageV2.ToolPart;
            break;
          }

          case "tool-result": {
            // 更新工具结果
            const match = toolcalls[value.toolCallId];
            await Session.updatePart({
              ...match,
              state: {
                status: "completed",
                input: value.input,
                output: value.output.output,
              },
            });
            delete toolcalls[value.toolCallId];
            break;
          }

          case "finish-step":
            // 创建快照
            snapshot = await Snapshot.track();
            break;
        }
      }
    },
  };
}
```

### 循环终止条件

- **完成响应** - 模型返回完整响应，无更多工具调用
- **用户中断** - abort 信号触发
- **错误发生** - 网络错误、模型错误等
- **Snapshot 失败** - 无法创建快照（可选）

---

## 5. 工具系统

### 工具定义方式

```typescript
// opencode/packages/opencode/src/tool/tool.ts
import { z } from "zod";

export interface Tool {
  name: string;
  description: string;
  parameters: z.ZodTypeAny;
  execute: (args: unknown, context: ToolContext) => Promise<ToolResult>;
}

export interface ToolResult {
  output: string;
  metadata?: Record<string, unknown>;
  title?: string;
  attachments?: Attachment[];
}

export interface ToolContext {
  sessionID: string;
  agent: Agent;
  permission: PermissionManager;
}
```

### 工具注册表位置

```
opencode/packages/opencode/src/tool/registry.ts
```

```typescript
export class ToolRegistry {
  private tools: Map<string, Tool> = new Map();

  register(tool: Tool): void {
    this.tools.set(tool.name, tool);
  }

  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }

  list(): Tool[] {
    return Array.from(this.tools.values());
  }

  getToolDefinitions(): ToolDefinition[] {
    return this.list().map((tool) => ({
      type: "function" as const,
      function: {
        name: tool.name,
        description: tool.description,
        parameters: zodToJsonSchema(tool.parameters),
      },
    }));
  }
}

// 全局注册表实例
export const registry = new ToolRegistry();
```

### 工具执行流程

```
模型返回 tool-call 事件
       │
       ▼
┌─────────────────┐
│ SessionProcessor│
│ process()       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 创建 ToolPart   │
│ 状态: pending   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 执行工具        │
│ Tool.execute()  │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐  ┌─────────────┐
│ 成功   │  │ 失败        │
└───┬────┘  └─────────────┘
    │            │
    ▼            ▼
┌─────────────────┐  ┌─────────────────┐
│ 状态: completed │  │ 状态: error     │
│ 发送 tool-result│  │ 发送 tool-error │
└────────┬────────┘  └─────────────────┘
         │
         ▼
┌─────────────────┐
│ 继续流式响应    │
│ (模型接收结果)  │
└─────────────────┘
```

### 审批机制

```typescript
// opencode/packages/opencode/src/permission/next.ts
export namespace PermissionNext {
  export async function ask(input: {
    permission: string;
    patterns: string[];
    sessionID: string;
    metadata?: Record<string, unknown>;
    always?: string[];
    ruleset: PermissionRuleset;
  }): Promise<void> {
    // 检查规则集
    if (ruleset.isAllowed(input.permission, input.patterns)) {
      return; // 自动批准
    }

    // 检查 "always" 列表
    if (input.always?.includes(input.patterns[0])) {
      return; // 自动批准
    }

    // 显示审批请求
    const response = await promptUser({
      type: "permission",
      permission: input.permission,
      metadata: input.metadata,
    });

    if (!response.approved) {
      throw new RejectedError(`Permission denied: ${input.permission}`);
    }
  }

  export class RejectedError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "RejectedError";
    }
  }
}
```

---

## 6. 状态管理

### Session 状态存储位置

```
opencode/packages/opencode/src/session/session.ts
opencode/packages/opencode/src/storage/db.ts
```

```typescript
// session/session.ts
export interface Session {
  id: string;
  name: string;
  agent: string;
  createdAt: Date;
  updatedAt: Date;
  messages: MessageV2[];
  metadata?: SessionMetadata;
}

// storage/db.ts - SQLite 数据库
export const Database = {
  Client() {
    return createClient({
      url: `file:${Global.Path.data}/opencode.db`,
    });
  },
};

// Drizzle ORM 定义
export const sessions = sqliteTable("sessions", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  agent: text("agent").notNull(),
  createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  updatedAt: integer("updated_at", { mode: "timestamp" }).notNull(),
});

export const messages = sqliteTable("messages", {
  id: text("id").primaryKey(),
  sessionId: text("session_id").references(() => sessions.id),
  role: text("role").notNull(), // user, assistant, system, tool
  content: text("content"),
  parts: text("parts", { mode: "json" }), // MessageV2 parts
  createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
});
```

### Checkpoint 机制

**Git Snapshot**:

```typescript
// opencode/packages/opencode/src/snapshot/index.ts
export namespace Snapshot {
  export async function track(): Promise<string> {
    // 获取当前 Git 状态
    const snapshot = await createSnapshot();

    // 保存到数据库
    await db.insert(snapshots).values({
      id: snapshot.id,
      hash: snapshot.hash,
      diff: snapshot.diff,
      createdAt: new Date(),
    });

    return snapshot.id;
  }

  export async function restore(snapshotId: string): Promise<void> {
    const snapshot = await db.query.snapshots.findFirst({
      where: eq(snapshots.id, snapshotId),
    });

    if (!snapshot) {
      throw new Error(`Snapshot not found: ${snapshotId}`);
    }

    // 应用 Git 补丁
    await applySnapshot(snapshot);
  }

  async function createSnapshot() {
    // 获取 Git diff
    const diff = await $`git diff`.text();

    // 获取当前 HEAD
    const hash = await $`git rev-parse HEAD`.text();

    return {
      id: generateId(),
      hash: hash.trim(),
      diff,
    };
  }
}
```

### 历史记录管理

**MessageV2 结构**:

```typescript
// opencode/packages/opencode/src/session/message-v2.ts
export namespace MessageV2 {
  export interface Base {
    id: string;
    sessionID: string;
    agent?: string;
    createdAt: Date;
    updatedAt: Date;
  }

  export interface User extends Base {
    role: "user";
    content: string | ContentPart[];
  }

  export interface Assistant extends Base {
    role: "assistant";
    parts: Part[];
    finish?: FinishReason;
    usage?: TokenUsage;
  }

  export type Part =
    | TextPart
    | ReasoningPart
    | ToolPart
    | StepStartPart;

  export interface ToolPart {
    id: string;
    type: "tool";
    tool: string;
    callID: string;
    state: ToolState;
    metadata?: Record<string, unknown>;
  }

  export interface ToolState {
    status: "pending" | "running" | "completed" | "error";
    input: Record<string, unknown>;
    output?: string;
    error?: string;
    time?: { start: number; end?: number };
    attachments?: Attachment[];
  }
}
```

### 状态恢复方式

```typescript
// 恢复会话
export async function resumeSession(sessionId: string): Promise<Session> {
  // 从数据库加载会话
  const session = await db.query.sessions.findFirst({
    where: eq(sessions.id, sessionId),
    with: {
      messages: true,
    },
  });

  if (!session) {
    throw new Error(`Session not found: ${sessionId}`);
  }

  // 恢复消息
  const messages = session.messages.map((m) => ({
    ...m,
    parts: m.parts ? JSON.parse(m.parts) : undefined,
  }));

  return {
    ...session,
    messages,
  };
}
```

---

## 7. 模型调用方式

### 支持的模型提供商

通过 Vercel AI SDK 支持多种提供商：

- **OpenAI** - GPT-4, GPT-3.5
- **Anthropic** - Claude 系列
- **Google** - Gemini 系列
- **本地模型** - Ollama, LM Studio 等
- **其他** - 任何兼容 OpenAI API 的提供商

### 模型调用封装位置

```
opencode/packages/opencode/src/session/llm.ts
```

```typescript
import { streamText, generateText } from "ai";

export namespace LLM {
  export interface StreamInput {
    model: Provider.Model;
    messages: CoreMessage[];
    tools?: ToolDefinition[];
    abort: AbortSignal;
  }

  export async function stream(input: StreamInput) {
    const result = streamText({
      model: input.model,
      messages: input.messages,
      tools: input.tools,
      abortSignal: input.abort,
      // 多步推理（工具循环）
      maxSteps: 10,
      // 工具执行函数
      experimental_activeTools: input.tools?.map((t) => t.function.name),
    });

    return {
      fullStream: result.fullStream,
      text: result.text,
      usage: result.usage,
    };
  }

  export async function generate(input: Omit<StreamInput, "abort">) {
    const result = await generateText({
      model: input.model,
      messages: input.messages,
      tools: input.tools,
    });

    return {
      text: result.text,
      toolCalls: result.toolCalls,
      usage: result.usage,
    };
  }
}
```

### 流式响应处理

```typescript
// processor.ts 中的流处理
const stream = await LLM.stream(streamInput);

for await (const value of stream.fullStream) {
  switch (value.type) {
    case "start":
      SessionStatus.set(input.sessionID, { type: "busy" });
      break;

    case "reasoning-start":
      // 创建推理部分
      const reasoningPart = {
        id: Identifier.ascending("part"),
        type: "reasoning" as const,
        text: "",
        time: { start: Date.now() },
      };
      reasoningMap[value.id] = reasoningPart;
      await Session.updatePart(reasoningPart);
      break;

    case "reasoning-delta":
      // 累积推理文本
      if (value.id in reasoningMap) {
        const part = reasoningMap[value.id];
        part.text += value.text;
        await Session.updatePartDelta({
          sessionID: part.sessionID,
          messageID: part.messageID,
          partID: part.id,
          field: "text",
          delta: value.text,
        });
      }
      break;

    case "tool-input-start":
      // 工具调用开始
      const part = await Session.updatePart({
        type: "tool",
        tool: value.toolName,
        callID: value.id,
        state: { status: "pending", input: {}, raw: "" },
      });
      toolcalls[value.id] = part as MessageV2.ToolPart;
      break;

    case "tool-call":
      // 执行工具
      const match = toolcalls[value.toolCallId];
      await Session.updatePart({
        ...match,
        state: { status: "running", input: value.input, time: { start: Date.now() } },
      });

      // Doom loop 检测
      await checkDoomLoop(toolcalls, value);
      break;

    case "tool-result":
      // 工具结果
      const toolPart = toolcalls[value.toolCallId];
      await Session.updatePart({
        ...toolPart,
        state: {
          status: "completed",
          input: value.input,
          output: value.output.output,
          time: { start: toolPart.state.time.start, end: Date.now() },
        },
      });
      delete toolcalls[value.toolCallId];
      break;

    case "finish-step":
      // 步骤完成，创建快照
      snapshot = await Snapshot.track();
      break;

    case "error":
      throw value.error;
  }
}
```

### Token 管理

```typescript
// opencode/packages/opencode/src/session/usage.ts
export function getUsage(input: {
  model: Provider.Model;
  usage?: LanguageModelUsage;
  metadata?: Record<string, unknown>;
}): TokenUsage {
  return {
    promptTokens: input.usage?.promptTokens ?? 0,
    completionTokens: input.usage?.completionTokens ?? 0,
    totalTokens: input.usage?.totalTokens ?? 0,
    // 计算成本
    cost: calculateCost(input.model, input.usage),
  };
}

// 存储使用统计
export const usage = sqliteTable("usage", {
  id: text("id").primaryKey(),
  sessionId: text("session_id").references(() => sessions.id),
  messageId: text("message_id").references(() => messages.id),
  promptTokens: integer("prompt_tokens").notNull(),
  completionTokens: integer("completion_tokens").notNull(),
  totalTokens: integer("total_tokens").notNull(),
  cost: real("cost"),
  createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
});
```

---

## 8. 数据流转图

```
┌────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                    │
└────────────────────────────────────────────────────────────────────────┘

用户输入 (CLI/TUI/Web)
       │
       ▼
┌─────────────────┐
│ index.ts        │  ──▶  packages/opencode/src/index.ts
│ yargs 解析      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ RunCommand /    │
│ TuiThreadCmd    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Session.create()                    │
│                                     │
│ 1. 创建 Session 记录                │
│    ┌─────────────┐                  │
│    │ SQLite      │                  │
│    │ sessions    │                  │
│    │ table       │                  │
│    └─────────────┘                  │
│                                     │
│ 2. 加载 Agent 配置                  │
│    ┌─────────────┐                  │
│    │ Agent       │                  │
│    │ config      │                  │
│    └─────────────┘                  │
│                                     │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ SessionProcessor.create()           │  ──▶  processor.ts:26
│                                     │
│ const processor = {                 │
│   async process(streamInput) {      │
│     const stream = await            │
│       LLM.stream(streamInput)       │
│            │                        │
│            ▼                        │
│       ┌─────────────┐               │
│       │ Vercel AI   │               │
│       │ SDK         │               │
│       │ streamText  │               │
│       └──────┬──────┘               │
│              │                      │
│              ▼                      │
│       流式响应                      │
│              │                      │
│              ▼                      │
│       ┌─────────────┐               │
│       │ for await   │               │
│       │ (value of   │               │
│       │  stream)    │               │
│       └──────┬──────┘               │
│              │                      │
│         ┌────┴────┐                 │
│         ▼         ▼                 │
│    ┌────────┐  ┌──────────┐         │
│    │文本    │  │ Tool Call│         │
│    │delta   │  │          │         │
│    └───┬────┘  └────┬─────┘         │
│        │            │               │
│        ▼            ▼               │
│    ┌────────┐  ┌──────────┐         │
│    │Session │  │ Tool     │         │
│    │update  │  │ execute  │         │
│    │Part    │  │          │         │
│    │Delta   │  │          │         │
│    └────────┘  └──────────┘         │
│                                     │
│    ┌─────────────┐                  │
│    │ finish-step │                  │
│    │ 触发        │                  │
│    │ Snapshot    │                  │
│    │ .track()    │                  │
│    └─────────────┘                  │
│         │                           │
│         ▼                           │
│    ┌─────────────┐                  │
│    │ Git 快照    │                  │
│    │ 保存状态    │                  │
│    └─────────────┘                  │
│                                     │
│   }                                 │
│ }                                   │
│                                     │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ 保存到数据库    │
│ messages table  │
└─────────────────┘
```

### 关键数据结构定义

```typescript
// Session 类型
interface Session {
  id: string;
  name: string;
  agent: string;
  createdAt: Date;
  updatedAt: Date;
  messages: MessageV2[];
}

// MessageV2 类型
namespace MessageV2 {
  interface Assistant {
    id: string;
    sessionID: string;
    role: "assistant";
    parts: Part[];
    finish?: "stop" | "length" | "tool-calls" | "error";
    usage?: TokenUsage;
  }

  type Part = TextPart | ReasoningPart | ToolPart | StepStartPart;

  interface ToolPart {
    id: string;
    type: "tool";
    tool: string;
    callID: string;
    state: {
      status: "pending" | "running" | "completed" | "error";
      input: Record<string, unknown>;
      output?: string;
      error?: string;
      time?: { start: number; end?: number };
    };
  }
}

// 流事件类型 (Vercel AI SDK)
type StreamEvent =
  | { type: "start" }
  | { type: "text-delta"; text: string }
  | { type: "reasoning-start"; id: string }
  | { type: "reasoning-delta"; id: string; text: string }
  | { type: "tool-input-start"; id: string; toolName: string }
  | { type: "tool-call"; toolCallId: string; toolName: string; input: unknown }
  | { type: "tool-result"; toolCallId: string; output: ToolResult }
  | { type: "finish-step"; finishReason: string; usage: LanguageModelUsage }
  | { type: "error"; error: Error };
```

---

## 9. 源码索引

### 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `src/index.ts` | 1 | yargs 配置 |
| SessionProcessor | `src/session/processor.ts` | 26 | 会话处理器 |
| process() | `src/session/processor.ts` | 45 | 流处理循环 |
| Session | `src/session/session.ts` | - | 会话管理 |
| MessageV2 | `src/session/message-v2.ts` | - | 消息类型 |
| LLM | `src/session/llm.ts` | - | 模型调用 |
| Agent | `src/agent/agent.ts` | - | Agent 定义 |
| ToolRegistry | `src/tool/registry.ts` | - | 工具注册 |
| Snapshot | `src/snapshot/index.ts` | - | Git 快照 |
| Database | `src/storage/db.ts` | - | SQLite 数据库 |

### 命令实现

| 命令 | 文件路径 | 说明 |
|------|----------|------|
| Run | `src/cli/cmd/run.ts` | 运行任务 |
| TUI | `src/cli/cmd/tui/thread.ts` | TUI 模式 |
| Agent | `src/cli/cmd/agent.ts` | Agent 管理 |
| MCP | `src/cli/cmd/mcp.ts` | MCP 命令 |

### 配置类

| 配置 | 文件路径 | 说明 |
|------|----------|------|
| Config | `src/config/config.ts` | 全局配置 |
| AgentConfig | `src/config/agent.ts` | Agent 配置 |
| Provider | `src/provider/provider.ts` | 模型提供商配置 |

### 数据库表

| 表 | 文件路径 | 说明 |
|------|----------|------|
| sessions | `src/storage/schema.ts` | 会话表 |
| messages | `src/storage/schema.ts` | 消息表 |
| snapshots | `src/storage/schema.ts` | 快照表 |
| usage | `src/storage/schema.ts` | 使用统计表 |

---

## 总结

opencode 是一个现代化的 TypeScript/Bun CLI Agent：

1. **多模型支持** - 通过 Vercel AI SDK 支持多种模型提供商
2. **Git 快照** - 创新的状态管理方式，基于 Git 实现
3. **SQLite 存储** - 本地数据库存储会话和消息
4. **TUI/Web 双模** - 支持终端界面和 Web 界面
5. **类型安全** - 完整的 TypeScript 类型支持
