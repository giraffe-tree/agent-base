# Qwen Code ACP 集成机制

## TL;DR（结论先行）

**一句话定义**：Qwen Code 实现了完整的 ACP (Agent Communication Protocol) 协议，通过 JSON-RPC 2.0 协议为 IDE 扩展（VSCode、Zed、JetBrains）提供标准化的 Agent 服务化能力，支持会话管理、流式状态更新、权限请求和子 Agent 任务分发。

**核心取舍**：**ACP 协议解耦 IDE 与 Agent 实现**（对比传统 CLI 交互方式），通过标准化 JSON-RPC 接口实现 IDE 与 Agent 的进程间通信，适合企业级 IDE 集成场景。

---

## 1. 为什么需要这个机制

### 1.1 问题场景

**没有 ACP 时的问题：**

```
IDE 想调用 Agent  →  解析 CLI 输出  →  处理复杂交互  →  难以维护
```

传统 CLI 输出难以被 IDE 程序化解析，状态更新、工具审批等交互难以实现。

**ACP 解决什么：**

```
IDE 通过 ACP 协议  →  标准化 JSON-RPC 通信  →  流式状态更新  →  完整的 Agent 能力
```

ACP 把"IDE 客户端"和"Agent 服务端"分离，通过标准化协议实现双向通信。

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| IDE 集成 | 每个 IDE 需要单独适配 CLI 输出格式，维护成本高 |
| 状态同步 | 无法实时获取 Agent 执行状态（思考过程、工具调用等） |
| 权限审批 | 工具执行需要用户确认时，无法优雅地中断并等待响应 |
| 多会话管理 | 单个 Agent 进程难以同时服务多个 IDE 会话 |
| 子 Agent 跟踪 | 复杂任务分解后，IDE 无法感知子任务执行状态 |

---

## 2. 整体架构

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ IDE / Editor (VSCode/Zed/JetBrains)                         │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ ACP Client                                              │ │
│ │ - acpConnection.ts: 建立 CLI 子进程连接                  │ │
│ │ - acpSessionManager.ts: 会话操作封装                     │ │
│ │ - acpMessageHandler.ts: 处理 Agent 通知                  │ │
│ └───────────────────────┬─────────────────────────────────┘ │
└───────────────────────┬─┼───────────────────────────────────┘
                        │ │ stdin/stdout
                        │ │ JSON-RPC 2.0
                        ▼ ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ ACP 集成层 ▓▓▓                                          │
│ Qwen Code CLI (ACP Server)                                   │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ AgentSideConnection (acp.ts)                            │ │
│ │ - JSON-RPC 消息路由                                      │ │
│ │ - 请求/通知分发                                          │ │
│ │ - 错误处理                                               │ │
│ └───────────────────────┬─────────────────────────────────┘ │
│                         │                                   │
│ ┌───────────────────────▼─────────────────────────────────┐ │
│ │ GeminiAgent (acpAgent.ts)                               │ │
│ │ - 多会话生命周期管理                                      │ │
│ │ - 认证处理                                               │ │
│ │ - MCP 配置集成                                           │ │
│ └───────────────────────┬─────────────────────────────────┘ │
│                         │                                   │
│ ┌───────────────────────▼─────────────────────────────────┐ │
│ │ Session (session/Session.ts)                            │ │
│ │ - Prompt 处理                                            │ │
│ │ - 工具调用                                               │ │
│ │ - 子 Agent 跟踪                                          │ │
│ └───────────────────────┬─────────────────────────────────┘ │
│                         │                                   │
│ ┌───────────────────────▼─────────────────────────────────┐ │
│ │ Agent Loop + MCP Servers                                │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `AgentSideConnection` | JSON-RPC 协议处理，消息路由 | `qwen-code/packages/cli/src/acp-integration/acp.ts:1` |
| `GeminiAgent` | 多会话管理、认证、配置集成 | `qwen-code/packages/cli/src/acp-integration/acpAgent.ts:1` |
| `Session` | 会话处理、工具调用、流式更新 | `qwen-code/packages/cli/src/acp-integration/session/Session.ts:1` |
| `SubAgentTracker` | 子 Agent 事件跟踪 | `qwen-code/packages/cli/src/acp-integration/session/SubAgentTracker.ts:1` |
| `acpConnection` (Client) | IDE 端连接管理 | `qwen-code/packages/vscode-ide-companion/src/services/acpConnection.ts:1` |

### 2.3 协议方法概览

| 方向 | 方法 | 说明 |
|-----|------|------|
| Client → Agent | `initialize` | 协议初始化，交换能力信息 |
| Client → Agent | `session/new` | 创建新会话 |
| Client → Agent | `session/prompt` | 发送用户输入 |
| Client → Agent | `session/cancel` | 取消当前生成 |
| Client → Agent | `session/set_mode` | 设置审批模式 |
| Agent → Client | `session/update` | 流式状态更新 |
| Agent → Client | `request_permission` | 请求工具执行权限 |
| Agent → Client | `authenticate/update` | 认证状态更新 |

---

## 3. 核心组件详细分析

### 3.1 AgentSideConnection - JSON-RPC 协议核心

#### 职责定位

一句话说明：`AgentSideConnection` 是 ACP 协议的传输层，负责 JSON-RPC 消息的序列化/反序列化、请求路由和错误处理。

#### 内部结构

```text
┌─────────────────────────────────────────────────────────────┐
│  AgentSideConnection (acp.ts)                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 输入层 (stdin)                                       │   │
│  │ - 逐行读取 JSON-RPC 消息                             │   │
│  │ - 解析请求/通知/响应                                 │   │
│  └───────────────────────┬─────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────┐   │
│  │ 路由层                                               │   │
│  │ - 根据 method 字段分发到对应处理器                   │   │
│  │ - 处理 initialize, session/* 等方法                  │   │
│  └───────────────────────┬─────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────┐   │
│  │ 响应层 (stdout)                                      │   │
│  │ - 序列化响应为 JSON                                  │   │
│  │ - 按行输出到 stdout                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `constructor()` | Agent factory, streams | Connection | 建立连接并启动消息循环 | `acp.ts:207` |
| `handler()` | method, params | result/error | 路由并处理请求 | `acp.ts:216` |
| `RequestError` | code, message | Error | 标准化错误响应 | `acp.ts:422` |

---

### 3.2 GeminiAgent - 多会话管理

#### 职责定位

一句话说明：`GeminiAgent` 是 ACP 协议的会话管理层，负责维护多个独立的 Session 实例，处理认证和配置集成。

#### 状态管理

```typescript
// qwen-code/packages/cli/src/acp-integration/acpAgent.ts
class GeminiAgent {
  private sessions: Map<string, Session> = new Map();
  private settings: Settings;

  async newSession({ cwd, mcpServers }: acp.NewSessionRequest): Promise<acp.NewSessionResponse> {
    // 1. 合并 MCP 配置（全局 + 会话级）
    const config = await this.newSessionConfig(cwd, mcpServers);
    // 2. 创建并存储 Session
    const session = await this.createAndStoreSession(config);
    return { sessionId: session.getId(), models: availableModels };
  }

  async prompt(params: acp.PromptRequest): Promise<acp.PromptResponse> {
    // 根据 sessionId 路由到对应 Session
    const session = this.sessions.get(params.sessionId);
    return session.prompt(params);
  }
}
```

每个 Session 拥有独立的：
- `GeminiChat` 实例（与 LLM 的对话上下文）
- `Config` 实例（包含 MCP Server 配置）
- 工具调用历史

---

### 3.3 Session - 会话处理核心

#### 职责定位

一句话说明：`Session` 是 ACP 协议的执行层，负责处理用户输入、工具调用、流式状态更新和子 Agent 跟踪。

#### 模块化事件发射器

```typescript
// qwen-code/packages/cli/src/acp-integration/session/Session.ts
private readonly historyReplayer: HistoryReplayer;    // 历史重放
private readonly toolCallEmitter: ToolCallEmitter;    // 工具调用事件
private readonly planEmitter: PlanEmitter;            // 计划更新事件
private readonly messageEmitter: MessageEmitter;      // 消息事件
```

这种设计使得状态更新逻辑清晰分离，便于维护和扩展。

#### 流式状态更新

```typescript
// qwen-code/packages/cli/src/acp-integration/session/Session.ts
async sendUpdate(update: acp.SessionUpdate): Promise<void> {
  const params: acp.SessionNotification = {
    sessionId: this.sessionId,
    update,
  };
  await this.client.sessionUpdate(params);
}
```

更新类型包括：
- `user_message_chunk`: 用户消息片段
- `agent_message_chunk`: Agent 回复片段（含 usage 元数据）
- `agent_thought_chunk`: Agent 思考过程
- `tool_call`: 工具调用开始
- `tool_call_update`: 工具调用更新/完成
- `plan`: 任务计划更新（来自 TodoWriteTool）
- `current_mode_update`: 审批模式变更
- `available_commands_update`: 可用命令列表

---

### 3.4 SubAgentTracker - 子 Agent 跟踪

#### 职责定位

一句话说明：`SubAgentTracker` 跟踪 TaskTool 创建的子 Agent 的工具调用事件，将子 Agent 状态通过 ACP 协议发送给客户端。

#### 跟踪流程

```typescript
// qwen-code/packages/cli/src/acp-integration/session/Session.ts
if (isTaskTool && 'eventEmitter' in invocation) {
  const taskEventEmitter = (invocation as { eventEmitter: SubAgentEventEmitter }).eventEmitter;
  const parentToolCallId = callId;
  const subagentType = (args['subagent_type'] as string) ?? '';

  const subAgentTracker = new SubAgentTracker(this, this.client, parentToolCallId, subagentType);
  subAgentCleanupFunctions = subAgentTracker.setup(taskEventEmitter, abortSignal);
}
```

子 Agent 事件通过 `sessionUpdate` 发送，包含 `parentToolCallId` 和 `subagentType` 元数据，使客户端能够构建层级视图。

---

## 4. 端到端数据流转

### 4.1 会话生命周期数据流

```
┌─────────────┐     initialize      ┌─────────────┐
│   Client    │ ──────────────────► │    Agent    │
│  (IDE)      │ ◄────────────────── │  (Qwen)     │
└─────────────┘   返回能力信息       └─────────────┘
       │                                    │
       │ authenticate                       │
       ├───────────────────────────────────►│
       │◄───────────────────────────────────┤
       │      认证成功/失败                  │
       │                                    │
       │ session/new (cwd, mcpServers)      │
       ├───────────────────────────────────►│
       │◄───────────────────────────────────┤
       │   返回 sessionId, availableModels   │
       │                                    │
       │ session/prompt                     │
       ├───────────────────────────────────►│
       │◄──────── session/update ───────────┤  流式
       │◄──────── session/update ───────────┤  更新
       │◄──────── session/update ───────────┤  ...
       │                                    │
       │◄────── request_permission ─────────┤  工具审批
       │ 返回审批结果 ──────────────────────►│
       │                                    │
       │◄──────── 最终 response ────────────┤
       │                                    │
```

### 4.2 子 Agent 任务分发流程

```
┌─────────────────────────────────────────────────────────────────┐
│                     子 Agent 任务分发流程                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   主 Session                                                    │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │  1. LLM 调用 TaskTool (subagent)                        │  │
│   │                                                         │  │
│   │  2. Session.runTool() 创建 SubAgentTracker              │  │
│   │     ┌─────────────────────────────────────────────┐    │  │
│   │     │ SubAgentTracker                             │    │  │
│   │     │  - parentToolCallId: "task-123"             │    │  │
│   │     │  - subagentType: "code_review"              │    │  │
│   │     │  - setup() 监听 SubAgentEventEmitter        │    │  │
│   │     └─────────────────────────────────────────────┘    │  │
│   │                         │                              │  │
│   │  3. 子 Agent 执行中...    ▼                              │  │
│   │     TaskTool.eventEmitter.emit('tool_call', {...})     │  │
│   │                         │                              │  │
│   │  4. SubAgentTracker 捕获事件 ◄─────────────────────────│  │
│   │     添加 parentToolCallId 和 subagentType 元数据        │  │
│   │                         │                              │  │
│   │  5. 通过 ToolCallEmitter 发送 session/update          │  │
│   │     包含 parentToolCallId 和 subagentType             │  │
│   │                         │                              │  │
│   │  6. Client 收到带元数据的工具调用更新                    │  │
│   │     可展示子 Agent 层级关系                            │  │
│   │                                                         │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   SessionUpdateMeta 中的子 Agent 相关字段：                      │
│   • parentToolCallId: 父工具调用 ID                             │
│   • subagentType: 子 Agent 类型 (如 "code_review")              │
│   • toolName: 实际执行的工具名称                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 数据变换详情

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| ACP 启动 | `--acp` 参数 | 解析配置，启动 ACP 模式 | ACP Server 就绪 | `config.ts:180` |
| 连接建立 | stdin/stdout | 创建 AgentSideConnection | Connection 对象 | `acp.ts:207` |
| 会话创建 | cwd, mcpServers | 合并配置，创建 Session | sessionId | `acpAgent.ts:246` |
| Prompt 处理 | 用户输入 | Agent Loop 执行 | 流式更新 | `Session.ts:298` |
| 工具调用 | tool_call 请求 | 执行工具，发送更新 | tool_call_update | `Session.ts:323` |
| 权限请求 | confirmationDetails | 构造权限请求 | request_permission | `Session.ts:323` |

---

## 5. 关键代码实现

### 5.1 核心数据结构

ACP 协议类型定义（Zod 校验）：

```typescript
// qwen-code/packages/cli/src/acp-integration/schema.ts
export const initializeRequestSchema = z.object({
  protocolVersion: z.string(),
  capabilities: z.object({
    authentication: z.boolean().optional(),
  }),
  clientInfo: z.object({
    name: z.string(),
    version: z.string(),
  }),
});

export const newSessionRequestSchema = z.object({
  cwd: z.string(),
  mcpServers: z.array(mcpServerSchema).optional(),
});

export const sessionUpdateSchema = z.object({
  type: z.enum([
    'user_message_chunk',
    'agent_message_chunk',
    'agent_thought_chunk',
    'tool_call',
    'tool_call_update',
    'plan',
    'current_mode_update',
    'available_commands_update',
  ]),
  content: z.unknown(),
});
```

### 5.2 主链路代码

ACP 协议启动入口：

```typescript
// qwen-code/packages/cli/src/config/config.ts
.option('acp', {
  type: 'boolean',
  description: 'Starts the agent in ACP mode',
  default: false,
})
.option('experimental-acp', {
  type: 'boolean',
  description: 'Starts the agent in ACP mode (deprecated, use --acp instead)',
  hidden: true,
})
```

入口路由逻辑：

```typescript
// qwen-code/packages/cli/src/gemini.tsx
if (config.getExperimentalZedIntegration()) {
  return runAcpAgent(config, settings, argv);
}
```

JSON-RPC 协议处理：

```typescript
// qwen-code/packages/cli/src/acp-integration/acp.ts
export class AgentSideConnection implements Client {
  #connection: Connection;

  constructor(
    toAgent: (conn: Client) => Agent,
    input: WritableStream<Uint8Array>,
    output: ReadableStream<Uint8Array>,
  ) {
    const agent = toAgent(this);
    const handler = async (method: string, params: unknown): Promise<unknown> => {
      switch (method) {
        case schema.AGENT_METHODS.initialize:
          return agent.initialize(schema.initializeRequestSchema.parse(params));
        case schema.AGENT_METHODS.session_new:
          return agent.newSession(schema.newSessionRequestSchema.parse(params));
        // ... other methods
      }
    };
    this.#connection = new Connection(handler, input, output);
  }
}
```

MCP 配置桥接：

```typescript
// qwen-code/packages/cli/src/acp-integration/acpAgent.ts
async newSessionConfig(cwd: string, mcpServers: acp.McpServer[]): Promise<Config> {
  const mergedMcpServers = { ...this.settings.merged.mcpServers };

  for (const { command, args, env: rawEnv, name } of mcpServers) {
    const env: Record<string, string> = {};
    for (const { name: envName, value } of rawEnv) {
      env[envName] = value;
    }
    mergedMcpServers[name] = new MCPServerConfig(command, args, env, cwd);
  }
  // ...
}
```

权限请求机制：

```typescript
// qwen-code/packages/cli/src/acp-integration/session/Session.ts
const params: acp.RequestPermissionRequest = {
  sessionId: this.sessionId,
  options: toPermissionOptions(confirmationDetails),
  toolCall: {
    toolCallId: callId,
    status: 'pending',
    title: invocation.getDescription(),
    content,
    locations: invocation.toolLocations(),
    kind: mappedKind,
  },
};

const output = await this.client.requestPermission(params);
```

### 5.3 关键调用链

```text
runAcpAgent()                  [gemini.tsx:196]
  -> new AgentSideConnection()  [acp.ts:207]
    -> GeminiAgent.initialize() [acpAgent.ts:1]
    -> GeminiAgent.newSession() [acpAgent.ts:246]
      -> new Session()          [session/Session.ts:1]
        -> session.prompt()     [session/Session.ts:255]
          -> Agent Loop 执行
            -> toolCallEmitter.emit()  [Session.ts:298]
              -> client.sessionUpdate() [acp.ts:1]
```

---

## 6. 设计意图与 Trade-off

### 6.1 ACP vs 传统 CLI 的选择

| 维度 | ACP 协议 | 传统 CLI | 取舍分析 |
|-----|---------|---------|---------|
| IDE 集成 | 标准化 JSON-RPC，易于解析 | 需要解析文本输出，脆弱 | ACP 更适合 IDE 场景 |
| 状态同步 | 流式更新，实时反馈 | 批量输出，延迟高 | ACP 用户体验更好 |
| 权限审批 | 协议级支持，优雅中断 | 需要交互式终端 | ACP 更适合 GUI 环境 |
| 多会话 | 单进程多会话，资源高效 | 多进程，资源开销大 | ACP 更适合服务端部署 |
| 复杂度 | 需要实现完整协议栈 | 简单直接 | CLI 更适合脚本场景 |
| 适用场景 | IDE 集成、企业级部署 | 命令行脚本、简单任务 | 根据场景选择 |

### 6.2 为什么这样设计？

**核心问题**：如何在保持 Agent 核心简洁的同时，实现与 IDE 的深度集成？

**解决方案**：
- 代码依据：`qwen-code/packages/cli/src/acp-integration/acp.ts:1-100`
- 设计意图：通过 JSON-RPC 协议标准化，解耦 IDE 客户端与 Agent 服务端
- 带来的好处：
  - IDE 可以实时获取 Agent 执行状态
  - 支持工具执行权限的优雅审批
  - 单进程可同时服务多个 IDE 会话
  - 子 Agent 状态可被 IDE 感知
- 付出的代价：
  - 需要实现完整的 ACP 协议栈
  - 协议版本兼容性维护成本
  - 调试复杂度增加

### 6.3 Qwen Code vs Kimi CLI ACP 实现对比

| 维度 | Qwen Code | Kimi CLI |
|------|-----------|----------|
| **协议传输** | stdin/stdout 流 | stdin/stdout 流 |
| **IDE 集成** | VSCode、Zed、JetBrains | VSCode |
| **子 Agent 创建** | TaskTool 内部实现 | 通过 ACP 协议创建独立进程 |
| **MCP 配置传递** | session/new 时传入 | session/new 时传入 |
| **权限审批** | request_permission 方法 | request_permission 方法 |
| **文件系统代理** | 支持（fs/read_text_file, fs/write_text_file） | 支持 |

### 6.4 ACP 支持对比（跨项目）

| 项目 | ACP 支持 | 实现方式 | 多 Agent 支持 |
|------|---------|---------|--------------|
| **Qwen Code** | **是** | 完整的 JSON-RPC 2.0 ACP 协议 | 子 Agent 通过 TaskTool |
| Kimi CLI | 是 | JSON-RPC ACP 协议 | 子 Agent 通过 ACP 创建 |
| Codex | 否 | - | 单 Agent |
| Gemini CLI | 否 | - | 单 Agent |
| OpenCode | 否 | - | 内置多 Agent（Build/Plan/Explore），非 ACP |
| SWE-agent | 否 | - | 单 Agent |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 连接断开 | stdin/stdout 流关闭 | `acp.ts:207` |
| 会话超时 | 长时间无活动 | `Session.ts:1` |
| 认证失败 | 认证请求被拒绝 | `acpAgent.ts:1` |
| 协议错误 | 收到非 JSON-RPC 格式数据 | `acp.ts:216` |
| 会话取消 | Client 发送 session/cancel | `Session.ts:255` |

### 7.2 超时/资源限制

```typescript
// qwen-code/packages/cli/src/acp-integration/session/Session.ts
// 工具调用超时由 Agent Loop 控制
// 会话级超时由 Session 管理
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 连接断开 | 清理会话资源，通知 Client | `acp.ts:207` |
| 协议错误 | 返回 JSON-RPC 错误响应 | `acp.ts:422` |
| 会话不存在 | 返回 SESSION_NOT_FOUND 错误 | `acpAgent.ts:255` |
| 工具执行错误 | 透传给 Agent，由 Agent 决定如何处理 | `Session.ts:298` |
| 权限拒绝 | 返回 REJECTED 状态，终止工具调用 | `Session.ts:336` |

### 7.4 向后兼容性

`--experimental-acp` 参数已弃用，但仍支持，并显示警告：

```typescript
// qwen-code/packages/cli/src/config/config.ts
if (result['experimentalAcp']) {
  writeStderrLine('⚠ Warning: --experimental-acp is deprecated...');
  if (!result['acp']) {
    result['acp'] = true;
  }
}
```

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `qwen-code/packages/cli/src/config/config.ts` | 180 | `--acp` 参数定义 |
| 入口 | `qwen-code/packages/cli/src/gemini.tsx` | 196 | ACP 模式路由 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acp.ts` | 207 | `AgentSideConnection` 类定义 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acp.ts` | 216 | JSON-RPC 请求路由 handler |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acp.ts` | 422 | `RequestError` 错误处理 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acpAgent.ts` | 1 | `GeminiAgent` 类定义 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acpAgent.ts` | 246 | `newSession()` 会话创建 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/acpAgent.ts` | 273 | `newSessionConfig()` MCP 配置合并 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/session/Session.ts` | 1 | `Session` 类定义 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/session/Session.ts` | 298 | `sendUpdate()` 流式更新 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/session/Session.ts` | 323 | 权限请求构造 |
| 核心 | `qwen-code/packages/cli/src/acp-integration/session/SubAgentTracker.ts` | 1 | `SubAgentTracker` 子 Agent 跟踪 |
| 协议 | `qwen-code/packages/cli/src/acp-integration/schema.ts` | 1 | ACP 协议类型定义 |
| 客户端 | `qwen-code/packages/vscode-ide-companion/src/services/acpConnection.ts` | 1 | VSCode 扩展连接管理 |
| 客户端 | `qwen-code/packages/vscode-ide-companion/src/services/acpSessionManager.ts` | 1 | VSCode 扩展会话操作 |

---

## 9. 延伸阅读

- 前置知识：`docs/comm/04-comm-agent-loop.md` —— 了解 ACP 如何与 Agent Loop 集成
- 相关机制：`docs/comm/06-comm-mcp-integration.md` —— ACP 与 MCP 的关系
- 相关机制：`docs/comm/comm-what-is-acp.md` —— ACP 协议概述
- 深度分析：各项目 ACP 实现对比
  - `docs/kimi-cli/13-kimi-cli-acp-integration.md`
  - `docs/codex/13-codex-acp-integration.md`
  - `docs/gemini-cli/13-gemini-cli-acp-integration.md`
  - `docs/opencode/13-opencode-acp-integration.md`
  - `docs/swe-agent/13-swe-agent-acp-integration.md`

---

*✅ Verified: 基于 qwen-code/packages/cli/src/acp-integration/ 源码分析*
*基于版本：2026-02-08 | 最后更新：2026-02-28*
