# gemini-cli 概述文档

## 1. 项目简介

**gemini-cli** 是 Google 推出的官方 CLI Agent，基于 TypeScript/Node.js 实现，提供与 Google Gemini 模型交互的命令行工具。

### 项目定位和目标
- Google 官方 Gemini CLI 工具
- 支持 Google Gemini 系列模型（Gemini Pro, Flash 等）
- 提供丰富的内置工具（代码编辑、文件操作、搜索等）
- 支持自定义技能（Skills）和命令
- 企业级功能（ checkpoint、hooks、策略控制）

### 技术栈
- **语言**: TypeScript
- **运行时**: Node.js 20+
- **核心依赖**:
  - `@google/genai` - Google GenAI SDK
  - `commander` - CLI 框架
  - `zod` - 数据验证
  - `picocolors` - 终端颜色
  - `marked` - Markdown 渲染

### 官方仓库
- https://github.com/google-gemini/gemini-cli
- 文档: https://github.com/google-gemini/gemini-cli/tree/main/docs

---

## 2. 架构概览

### 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI Layer                              │
│  (gemini-cli/packages/cli/index.ts:1)                      │
│  ├─ 异常处理 (uncaughtException)                            │
│  ├─ main() 入口                                             │
│  └─ 子命令: chat, config, skills, etc                       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  Commands Layer                             │
│  (gemini-cli/packages/cli/src/gemini.ts)                   │
│  ├─ 命令解析 (Commander)                                    │
│  ├─ 配置管理                                                │
│  └─ 子命令分发                                              │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  GeminiClient Layer                         │
│  (gemini-cli/packages/core/src/core/client.ts:83)          │
│  ├─ GeminiClient: 主客户端类                                │
│  ├─ sendMessageStream(): 流式消息处理                       │
│  └─ processTurn(): 单回合处理                               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Turn Layer                               │
│  (gemini-cli/packages/core/src/core/turn.ts)               │
│  ├─ Turn: 回合管理                                          │
│  ├─ GeminiEventType: 事件类型                               │
│  └─ 工具调用队列                                            │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Tools Layer                              │
│  (gemini-cli/packages/core/src/tools/)                     │
│  ├─ tool-registry.ts - 工具注册                             │
│  ├─ scheduler.ts - 工具调度                                 │
│  └─ handlers/ - 工具实现                                    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  GeminiChat Layer                           │
│  (gemini-cli/packages/core/src/core/geminiChat.ts)         │
│  ├─ 模型调用封装                                            │
│  ├─ 流式响应处理                                            │
│  └─ Token 管理                                              │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  Checkpoint Layer                           │
│  (gemini-cli/packages/core/src/utils/checkpointUtils.ts)   │
│  ├─ 状态持久化                                              │
│  ├─ 会话恢复                                                │
│  └─ 压缩管理                                                │
└─────────────────────────────────────────────────────────────┘
```

### 各层职责说明

| 层级 | 文件路径 | 核心职责 |
|------|----------|----------|
| CLI | `packages/cli/index.ts` | 入口、异常处理、主函数调用 |
| Commands | `packages/cli/src/gemini.ts` | 命令解析、配置管理、子命令分发 |
| Client | `packages/core/src/core/client.ts` | Agent 核心、事件循环、流处理 |
| Turn | `packages/core/src/core/turn.ts` | 回合管理、事件类型、工具队列 |
| Tools | `packages/core/src/tools/` | 工具注册、调度、执行 |
| Chat | `packages/core/src/core/geminiChat.ts` | 模型调用、流式响应 |
| Checkpoint | `packages/core/src/utils/checkpointUtils.ts` | 状态持久化、会话恢复 |

### 核心组件列表

1. **GeminiClient** (core/client.ts:83) - 主客户端，管理事件循环
2. **GeminiChat** (core/geminiChat.ts) - 模型调用封装
3. **Turn** (core/turn.ts) - 回合管理
4. **ToolRegistry** (tools/tool-registry.ts) - 工具注册表
5. **ToolScheduler** (tools/scheduler.ts) - 工具调度器
6. **Config** (config/config.ts) - 配置管理

---

## 3. 入口与 CLI

### 入口文件路径
```
gemini-cli/packages/cli/index.ts:1
gemini-cli/packages/cli/src/gemini.ts (主逻辑)
```

### CLI 参数解析方式

使用 `commander` 库进行命令解析：

```typescript
// packages/cli/src/gemini.ts
import { Command } from 'commander';

const program = new Command()
  .name('gemini')
  .description('Google Gemini CLI')
  .version(VERSION)
  .option('-m, --model <model>', 'Model to use')
  .option('--yolo', 'Auto-approve all actions')
  .option('-c, --config <path>', 'Config file path');

// 子命令
program
  .command('chat')
  .description('Start interactive chat')
  .action(async () => {
    await startChat();
  });

program
  .command('config')
  .description('Manage configuration')
  .action(async () => {
    await manageConfig();
  });
```

### 启动流程

```
packages/cli/index.ts:14
       │
       ├─ 注册全局异常处理
       │   ├─ uncaughtException
       │   └─ unhandledRejection
       │
       ▼
main() (gemini.ts)
       │
       ├─ 解析命令行参数 (Commander)
       │
       ├─ 加载配置
       │   └─ Config.load()
       │
       ├─ match 子命令:
       │   ├─ "chat" ──▶ startInteractiveChat()
       │   ├─ "skills" ──▶ manageSkills()
       │   └─ ...
       │
       └─ 无子命令 ──▶ 默认进入交互模式
           │
           ▼
       ┌─────────────────┐
       │ GeminiClient    │
       │ ::initialize()  │
       │ ::sendMessageStream()
       └─────────────────┘
```

---

## 4. Agent 循环机制

### 主循环代码位置

```
gemini-cli/packages/core/src/core/client.ts:83 (GeminiClient)
gemini-cli/packages/core/src/core/client.ts:350+ (sendMessageStream)
gemini-cli/packages/core/src/core/client.ts:450+ (processTurn)
```

### 流程图（文本形式）

```
┌─────────────────┐
│ GeminiClient    │
│ ::initialize()  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ startChat()     │
│ 启动聊天        │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│       交互循环                        │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 等待用户输入                   │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ sendMessageStream(userInput)  │  │  ──▶ client.ts:350
│  │                               │  │
│  │ 1. fireBeforeAgentHook()      │  │
│  │ 2. buildContent()             │  │
│  │ 3. 调用 chat.sendMessageStream│  │
│  │ 4. processTurn()              │  │  ──▶ client.ts:450
│  │                               │  │
│  └───────────────────────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ processTurn()                 │  │
│  │                               │  │
│  │ while turn.hasPendingToolCalls│  │
│  │   ┌───────────────────────┐   │  │
│  │   │ 1. 获取工具调用请求   │   │  │
│  │   │ 2. ToolScheduler.     │   │  │
│  │   │    executePendingCalls│   │  │
│  │   │ 3. 发送工具结果到模型 │   │  │
│  │   │ 4. 继续流式响应       │   │  │
│  │   └───────────────────────┘   │  │
│  │                               │  │
│  └───────────────────────────────┘  │
│                  │                  │
│                  ▼                  │
│  ┌───────────────────────────────┐  │
│  │ fireAfterAgentHook()          │  │
│  │ 显示响应给用户                │  │
│  └───────────────────────────────┘  │
│                                     │
└─────────────────────────────────────┘
         │
         ▼ (用户退出)
┌─────────────────┐
│ saveCheckpoint  │
│ 保存会话状态    │
└─────────────────┘
```

### 单次循环的执行步骤

**sendMessageStream()** (client.ts:350+):

```typescript
async function sendMessageStream(
  request: PartListUnion,
  promptId: string
): Promise<AsyncGenerator<ServerGeminiStreamEvent>> {
  // 1. 触发 BeforeAgent Hook
  const hookResult = await this.fireBeforeAgentHookSafe(request, promptId);
  if (hookResult?.type === GeminiEventType.AgentExecutionStopped) {
    return; // Hook 阻止执行
  }

  // 2. 构建消息内容
  const content = this.buildContent(request);

  // 3. 发送流式请求
  const stream = this.chat.sendMessageStream(content);

  // 4. 处理流式响应
  for await (const chunk of stream) {
    const event = this.parseChunk(chunk);

    switch (event.type) {
      case GeminiEventType.TextDelta:
        yield event;
        break;

      case GeminiEventType.ToolCall:
        // 添加工具调用到队列
        turn.addPendingToolCall(event.toolCall);
        yield event;
        break;

      case GeminiEventType.TurnComplete:
        // 处理工具调用
        if (turn.hasPendingToolCalls()) {
          await this.processTurn(turn);
        }
        yield event;
        break;
    }
  }

  // 5. 触发 AfterAgent Hook
  await this.fireAfterAgentHookSafe(request, promptId, turn);
}
```

**processTurn()** (client.ts:450+):

```typescript
async function processTurn(turn: Turn): Promise<void> {
  // 执行所有待处理的工具调用
  while (turn.hasPendingToolCalls()) {
    const toolCalls = turn.getPendingToolCalls();

    // 调度工具执行
    const results = await this.toolScheduler.executePendingCalls(toolCalls);

    // 发送工具结果到模型
    for (const result of results) {
      await this.chat.sendFunctionResult(result);
    }

    // 继续接收模型响应
    const stream = this.chat.receiveStream();
    for await (const chunk of stream) {
      // 处理新的响应（可能有更多工具调用）
      // ...
    }
  }
}
```

### 循环终止条件

- **无工具调用** - 模型返回纯文本，没有 functionCall
- **达到最大回合数** - MAX_TURNS (默认 100)
- **Hook 阻止** - BeforeAgentHook 返回 StopExecution
- **用户中断** - Ctrl+C 信号
- **Token 限制** - 达到上下文限制

---

## 5. 工具系统

### 工具定义方式

```typescript
// packages/core/src/tools/types.ts
interface Tool {
  name: string;
  description: string;
  parameters: z.ZodTypeAny;
  handler: ToolHandler;
}

type ToolHandler = (
  args: unknown,
  context: ToolContext
) => Promise<ToolResult>;

interface ToolResult {
  output: string;
  error?: string;
}
```

### 工具注册表位置

```
gemini-cli/packages/core/src/tools/tool-registry.ts
gemini-cli/packages/core/src/tools/scheduler.ts
```

```typescript
// tool-registry.ts
class ToolRegistry {
  private tools: Map<string, Tool> = new Map();

  register(tool: Tool): void {
    this.tools.set(tool.name, tool);
  }

  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }

  getAll(): Tool[] {
    return Array.from(this.tools.values());
  }

  getToolDefinitions(): FunctionDeclaration[] {
    // 转换为 Gemini API 格式
    return this.getAll().map(tool => ({
      name: tool.name,
      description: tool.description,
      parameters: zodToJsonSchema(tool.parameters),
    }));
  }
}

// scheduler.ts
class ToolScheduler {
  async executePendingCalls(
    toolCalls: ToolCall[],
  ): Promise<ToolResult[]> {
    // 并行执行工具调用
    const promises = toolCalls.map(call =>
      this.executeSingle(call)
    );
    return Promise.all(promises);
  }

  private async executeSingle(call: ToolCall): Promise<ToolResult> {
    const tool = this.registry.get(call.name);
    if (!tool) {
      return { output: '', error: `Tool not found: ${call.name}` };
    }

    // 解析和验证参数
    const args = tool.parameters.parse(call.args);

    // 执行工具
    const result = await tool.handler(args, this.context);

    return result;
  }
}
```

### 工具执行流程

```
模型返回 functionCall
       │
       ▼
┌─────────────────┐
│ Turn.add        │
│ PendingToolCall │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 流式响应结束    │
│ (TurnComplete)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ processTurn()   │
│ 处理待执行工具  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ToolScheduler   │
│ ::execute       │
│ PendingCalls()  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 并行执行工具    │
│ Promise.all()   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 发送结果到模型  │
│ chat.send       │
│ FunctionResult  │
└─────────────────┘
```

### 审批机制

```typescript
// packages/core/src/utils/approval.ts
class ApprovalManager {
  private autoApprove: boolean;
  private approvedTools: Set<string> = new Set();

  async requestApproval(
    toolName: string,
    args: unknown,
  ): Promise<boolean> {
    if (this.autoApprove || this.approvedTools.has(toolName)) {
      return true;
    }

    // 显示工具调用详情
    console.log(`\n${colors.yellow('Tool Call:')} ${toolName}`);
    console.log(colors.gray(JSON.stringify(args, null, 2)));

    // 询问用户
    const answer = await prompt('Approve? (y/n/always): ');

    if (answer === 'always') {
      this.approvedTools.add(toolName);
    }

    return answer === 'y' || answer === 'always';
  }
}
```

---

## 6. 状态管理

### Session 状态存储位置

```
gemini-cli/packages/core/src/utils/checkpointUtils.ts
gemini-cli/packages/core/src/core/turn.ts
```

```typescript
// checkpointUtils.ts
interface Checkpoint {
  sessionId: string;
  timestamp: number;
  history: Content[];
  config: ConfigSnapshot;
  compressed: boolean;
}

class CheckpointManager {
  private checkpointDir: string;

  async save(checkpoint: Checkpoint): Promise<void> {
    const path = path.join(
      this.checkpointDir,
      `checkpoint-${checkpoint.sessionId}.json`
    );
    await fs.writeFile(path, JSON.stringify(checkpoint, null, 2));
  }

  async load(sessionId: string): Promise<Checkpoint | null> {
    const path = path.join(
      this.checkpointDir,
      `checkpoint-${sessionId}.json`
    );
    try {
      const data = await fs.readFile(path, 'utf-8');
      return JSON.parse(data);
    } catch {
      return null;
    }
  }

  async list(): Promise<Checkpoint[]> {
    // 列出所有检查点
  }
}
```

### Checkpoint 机制

```
自动保存触发条件:
- 每 N 个回合
- 会话正常退出时
- 用户手动触发

保存内容:
┌─────────────────┐
│ Checkpoint      │
├─────────────────┤
│ - sessionId     │
│ - timestamp     │
│ - history[]     │  完整对话历史
│ - config        │  配置快照
│ - compressed    │  是否已压缩
└─────────────────┘

恢复流程:
1. 扫描 checkpoint 目录
2. 按时间排序
3. 加载指定 checkpoint
4. 恢复 chat history
5. 恢复 config
```

### 历史记录管理

```typescript
// packages/core/src/core/geminiChat.ts
class GeminiChat {
  private history: Content[] = [];

  addHistory(content: Content): void {
    this.history.push(content);
  }

  getHistory(): Content[] {
    return this.history;
  }

  getLastPromptTokenCount(): number {
    return this.lastPromptTokenCount;
  }

  stripThoughtsFromHistory(): void {
    // 移除思考内容，节省 Token
  }
}

// packages/core/src/core/turn.ts
class Turn {
  private pendingToolCalls: ToolCall[] = [];
  private responseText: string = '';

  addPendingToolCall(call: ToolCall): void {
    this.pendingToolCalls.push(call);
  }

  hasPendingToolCalls(): boolean {
    return this.pendingToolCalls.length > 0;
  }

  getPendingToolCalls(): ToolCall[] {
    const calls = [...this.pendingToolCalls];
    this.pendingToolCalls = [];
    return calls;
  }

  appendResponseText(text: string): void {
    this.responseText += text;
  }

  getResponseText(): string {
    return this.responseText;
  }
}
```

### 状态恢复方式

```typescript
// 恢复会话
async function resumeSession(sessionId: string): Promise<void> {
  const checkpoint = await checkpointManager.load(sessionId);
  if (!checkpoint) {
    throw new Error(`Session not found: ${sessionId}`);
  }

  // 恢复 chat
  const chat = new GeminiChat();
  for (const content of checkpoint.history) {
    chat.addHistory(content);
  }

  // 恢复 config
  const config = Config.fromSnapshot(checkpoint.config);

  // 创建 client
  const client = new GeminiClient(config, chat);
  await client.initialize();
}
```

---

## 7. 模型调用方式

### 支持的模型提供商

- **Google Gemini** - Gemini Pro, Flash, Ultra（默认）
- **Vertex AI** - Google Cloud 部署
- **Gemini API** - Google AI Studio

### 模型调用封装位置

```
gemini-cli/packages/core/src/core/geminiChat.ts
gemini-cli/packages/core/src/core/client.ts
```

```typescript
// geminiChat.ts
import { GoogleGenAI } from '@google/genai';

class GeminiChat {
  private genAI: GoogleGenAI;
  private model: string;
  private chat?: ChatSession;

  constructor(config: Config) {
    this.genAI = new GoogleGenAI({ apiKey: config.apiKey });
    this.model = config.model || 'gemini-2.0-flash';
  }

  async startChat(): Promise<ChatSession> {
    this.chat = this.genAI.chats.create({
      model: this.model,
      config: {
        tools: this.toolRegistry.getToolDefinitions(),
        systemInstruction: this.systemPrompt,
      },
    });
    return this.chat;
  }

  async sendMessageStream(
    content: PartListUnion
  ): Promise<AsyncGenerator<GenerateContentResponse>> {
    const stream = await this.chat.sendMessageStream({
      message: content,
    });
    return stream;
  }

  async sendFunctionResult(
    result: ToolResult
  ): Promise<void> {
    await this.chat.sendMessage({
      message: [{
        functionResponse: {
          name: result.toolName,
          response: { output: result.output },
        },
      }],
    });
  }
}
```

### 流式响应处理

```typescript
// client.ts 中的流处理
async function* handleStream(
  stream: AsyncGenerator<GenerateContentResponse>
): AsyncGenerator<ServerGeminiStreamEvent> {
  for await (const chunk of stream) {
    // 处理文本增量
    if (chunk.text) {
      yield {
        type: GeminiEventType.TextDelta,
        text: chunk.text,
      };
    }

    // 处理工具调用
    if (chunk.functionCalls) {
      for (const call of chunk.functionCalls) {
        yield {
          type: GeminiEventType.ToolCall,
          toolCall: {
            name: call.name,
            args: call.args,
          },
        };
      }
    }

    // 处理完成
    if (chunk.candidates?.[0]?.finishReason) {
      yield {
        type: GeminiEventType.TurnComplete,
        finishReason: chunk.candidates[0].finishReason,
      };
    }
  }
}
```

### Token 管理

```typescript
// packages/core/src/core/geminiChat.ts
class GeminiChat {
  private lastPromptTokenCount: number = 0;
  private totalTokenCount: number = 0;

  updateTokenCount(usage: TokenUsage): void {
    this.lastPromptTokenCount = usage.promptTokenCount;
    this.totalTokenCount += usage.totalTokenCount;
  }

  getLastPromptTokenCount(): number {
    return this.lastPromptTokenCount;
  }

  shouldCompress(): boolean {
    // 检查是否需要压缩历史
    return this.lastPromptTokenCount > COMPACT_THRESHOLD;
  }
}
```

---

## 8. 数据流转图

```
┌────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                    │
└────────────────────────────────────────────────────────────────────────┘

用户输入 (CLI)
       │
       ▼
┌─────────────────┐
│ gemini.ts       │  ──▶  packages/cli/src/gemini.ts
│ 命令解析        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ GeminiClient    │  ──▶  packages/core/src/core/client.ts:83
│ ::initialize()  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ startChat()     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ 用户输入循环                         │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ sendMessageStream(request)    │  │
│  │                               │  │
│  │ 1. fireBeforeAgentHook()      │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ HookSystem  │             │  │
│  │   └─────────────┘             │  │
│  │                               │  │
│  │ 2. chat.sendMessageStream()   │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ GeminiChat  │             │  │
│  │   │ (@google/   │             │  │
│  │   │  genai)     │             │  │
│  │   └──────┬──────┘             │  │
│  │          │                    │  │
│  │          ▼                    │  │
│  │      流式响应                  │  │
│  │          │                    │  │
│  │          ▼                    │  │
│  │   ┌─────────────┐             │  │
│  │   │ 解析 chunk  │             │  │
│  │   │ - text      │             │  │
│  │   │ - toolCall  │             │  │
│  │   │ - finish    │             │  │
│  │   └──────┬──────┘             │  │
│  │          │                    │  │
│  │     ┌────┴────┐               │  │
│  │     ▼         ▼               │  │
│  │ ┌───────┐  ┌──────────┐       │  │
│  │ │文本   │  │ ToolCall │       │  │
│  │ │显示   │  └────┬─────┘       │  │
│  │ └───┬───┘       │             │  │
│  │     │           ▼             │  │
│  │     │      ┌─────────────┐    │  │
│  │     │      │ Turn.add    │    │  │
│  │     │      │ PendingTool │    │  │
│  │     │      │ Call        │    │  │
│  │     │      └─────────────┘    │  │
│  │     │                         │  │
│  │ 3. processTurn() (如有工具)   │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ ToolSched   │             │  │
│  │   │ ::execute   │             │  │
│  │   │ PendingCalls│             │  │
│  │   └──────┬──────┘             │  │
│  │          │                    │  │
│  │          ▼                    │  │
│  │      执行工具                  │  │
│  │          │                    │  │
│  │          ▼                    │  │
│  │   ┌─────────────┐             │  │
│  │   │ 发送结果    │             │  │
│  │   │ 到模型      │             │  │
│  │   └─────────────┘             │  │
│  │                               │  │
│  │ 4. fireAfterAgentHook()       │  │
│  │                               │  │
│  └───────────────────────────────┘  │
│                                     │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ Checkpoint      │  ──▶  checkpointUtils.ts
│ 自动保存        │
└─────────────────┘
```

### 关键数据结构定义

```typescript
// 事件类型
enum GeminiEventType {
  TextDelta = 'text-delta',
  ToolCall = 'tool-call',
  ToolResult = 'tool-result',
  TurnComplete = 'turn-complete',
  AgentExecutionStopped = 'agent-stopped',
  AgentExecutionBlocked = 'agent-blocked',
}

interface ServerGeminiStreamEvent {
  type: GeminiEventType;
  text?: string;
  toolCall?: ToolCall;
  toolResult?: ToolResult;
  finishReason?: string;
}

// 工具调用
interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

interface ToolResult {
  toolCallId: string;
  output: string;
  error?: string;
}

// Hook 输出
interface BeforeAgentHookOutput {
  shouldStopExecution(): boolean;
  isBlockingDecision(): boolean;
  getAdditionalContext(): string | undefined;
  getEffectiveReason(): string;
  systemMessage?: string;
}
```

---

## 9. 源码索引

### 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `packages/cli/index.ts` | 1 | 主入口 |
| 命令解析 | `packages/cli/src/gemini.ts` | - | Commander 配置 |
| GeminiClient | `packages/core/src/core/client.ts` | 83 | 主客户端 |
| sendMessageStream | `packages/core/src/core/client.ts` | 350+ | 流式消息 |
| processTurn | `packages/core/src/core/client.ts` | 450+ | 回合处理 |
| Turn | `packages/core/src/core/turn.ts` | - | 回合管理 |
| GeminiChat | `packages/core/src/core/geminiChat.ts` | - | 模型封装 |
| ToolRegistry | `packages/core/src/tools/tool-registry.ts` | - | 工具注册 |
| ToolScheduler | `packages/core/src/tools/scheduler.ts` | - | 工具调度 |
| Checkpoint | `packages/core/src/utils/checkpointUtils.ts` | - | 检查点 |

### 配置类

| 配置 | 文件路径 | 说明 |
|------|----------|------|
| Config | `packages/core/src/config/config.ts` | 主配置 |
| ModelConfig | `packages/core/src/config/models.ts` | 模型配置 |

### 内置工具

| 工具 | 文件路径 | 说明 |
|------|----------|------|
| File | `packages/core/src/tools/handlers/file.ts` | 文件操作 |
| Shell | `packages/core/src/tools/handlers/shell.ts` | Shell 执行 |
| Search | `packages/core/src/tools/handlers/search.ts` | 搜索 |
| Code | `packages/core/src/tools/handlers/code.ts` | 代码编辑 |

---

## 总结

gemini-cli 是一个企业级 TypeScript CLI Agent：

1. **架构清晰** - CLI → Commands → Client → Turn → Tools → Chat
2. **Hook 系统** - 支持 Before/After Agent Hook 扩展
3. **工具调度** - 并行工具执行，智能调度
4. **Checkpoint** - 完善的会话持久化和恢复
5. **企业特性** - 策略控制、审批机制、压缩管理
