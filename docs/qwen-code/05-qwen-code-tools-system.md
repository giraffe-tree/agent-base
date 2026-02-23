# Tools 系统（Qwen Code）

本文分析 Qwen Code 的 Tools 系统，包括工具注册、发现、调度和执行机制。

---

## 1. 先看全局（流程图）

### 1.1 工具系统架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│  TOOL 定义层                                                         │
│  ┌─────────────────────────────────────────┐                        │
│  │ 内置工具 (Built-in Tools)               │                        │
│  │   ├── read-file, write-file, edit       │                        │
│  │   ├── ls, grep, glob                    │                        │
│  │   ├── shell, web-fetch                  │                        │
│  │   └── memory, todoWrite                 │                        │
│  │                                         │                        │
│  │ MCP 工具 (DiscoveredMCPTool)            │                        │
│  │   └── 动态发现自 MCP 服务器             │                        │
│  │                                         │                        │
│  │ 发现工具 (DiscoveredTool)               │                        │
│  │   └── 通过配置命令动态发现              │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TOOL 注册层: ToolRegistry                                           │
│  ┌─────────────────────────────────────────┐                        │
│  │ (packages/core/src/tools/              │                        │
│  │  tool-registry.ts:174)                  │                        │
│  │                                         │                        │
│  │  tools: Map<string, AnyDeclarativeTool> │                        │
│  │                                         │                        │
│  │  ┌─────────────────────────────────┐    │                        │
│  │  │ discoverAllTools()              │    │                        │
│  │  │   ├── removeDiscoveredTools()   │    │                        │
│  │  │   ├── discoverAndRegisterTools  │    │                        │
│  │  │   │   FromCommand()             │    │                        │
│  │  │   └── mcpClientManager.         │    │                        │
│  │  │       discoverAllMcpTools()     │    │                        │
│  │  └─────────────────────────────────┘    │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TOOL 调度层: CoreToolScheduler                                      │
│  ┌─────────────────────────────────────────┐                        │
│  │ (packages/core/src/core/               │                        │
│  │  coreToolScheduler.ts)                  │                        │
│  │                                         │                        │
│  │ 调度流程:                                │                        │
│  │   1. validateParams()  参数校验         │                        │
│  │   2. shouldConfirmExecute() 确认检查    │                        │
│  │   3. execute()  执行工具                │                        │
│  │   4. 返回 ToolResult                    │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 类/模块  ├──┤ 方法/步骤  ──► 调用关系
```

### 1.2 工具执行流程

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     工具调用生命周期                                    │
└──────────────────────────────────────────────────────────────────────┘

模型输出 functionCalls
         │
         ▼
┌─────────────────┐
│ Turn.run()      │ ◄── 解析 functionCalls
│ 产出 ToolCall   │     创建 ToolCallRequest
│ Request 事件    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ UI 层收集       │
│ pending calls   │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ scheduleToolCalls()     │ ◄── 用户确认/自动批准
│ 检查 approval mode      │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ CoreToolScheduler       │
│ .executePendingCalls()  │
└────────┬────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────┐
│validate│ │should  │
│Params  │ │Confirm │
└───┬────┘ └───┬────┘
    │          │
    └────┬─────┘
         ▼
┌─────────────────┐
│ tool.execute()  │ ◄── 实际执行
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 生成            │
│ functionResponse│ ◄── 返回模型
└─────────────────┘
         │
         ▼
┌─────────────────┐
│ 递归调用        │ ◄── 新一轮
│ sendMessageStream│     sendMessageStream
└─────────────────┘
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道 ToolRegistry 管理工具注册，CoreToolScheduler 负责调度。
- **3 分钟版**：看 `1.1` + `1.2` + `3.1` 节，了解工具发现和执行流程。
- **10 分钟版**：通读全文，掌握内置工具、MCP 工具、自定义工具的实现。

### 2.1 一句话定义

Qwen Code 的 Tools 系统是「**注册表 + 调度器**」双层架构：ToolRegistry 负责工具注册和发现，CoreToolScheduler 负责参数校验、用户确认和实际执行。

---

## 3. 核心组件

### 3.1 ToolRegistry

✅ **Verified**: `qwen-code/packages/core/src/tools/tool-registry.ts:174`

```typescript
export class ToolRegistry {
  private tools: Map<string, AnyDeclarativeTool> = new Map();
  private mcpClientManager: McpClientManager;

  constructor(
    config: Config,
    eventEmitter?: EventEmitter,
    sendSdkMcpMessage?: SendSdkMcpMessage,
  ) {
    this.config = config;
    this.mcpClientManager = new McpClientManager(
      this.config, this, eventEmitter, sendSdkMcpMessage
    );
  }

  // 注册工具
  registerTool(tool: AnyDeclarativeTool): void {
    if (this.tools.has(tool.name)) {
      if (tool instanceof DiscoveredMCPTool) {
        tool = tool.asFullyQualifiedTool();  // MCP 工具使用完全限定名
      } else {
        debugLogger.warn(`Tool "${tool.name}" already registered. Overwriting.`);
      }
    }
    this.tools.set(tool.name, tool);
  }

  // 发现所有工具（内置 + MCP + 命令发现）
  async discoverAllTools(): Promise<void> {
    this.removeDiscoveredTools();
    this.config.getPromptRegistry().clear();

    // 从命令发现工具
    await this.discoverAndRegisterToolsFromCommand();

    // 从 MCP 服务器发现工具
    await this.mcpClientManager.discoverAllMcpTools(this.config);
  }

  // 仅发现 MCP 工具
  async discoverMcpTools(): Promise<void> {
    this.removeDiscoveredTools();
    this.config.getPromptRegistry().clear();
    await this.mcpClientManager.discoverAllMcpTools(this.config);
  }

  // 获取工具 schema（用于 API 调用）
  getFunctionDeclarations(): FunctionDeclaration[] {
    const declarations: FunctionDeclaration[] = [];
    this.tools.forEach((tool) => {
      declarations.push(tool.schema);
    });
    return declarations;
  }

  // 获取单个工具
  getTool(name: string): AnyDeclarativeTool | undefined {
    return this.tools.get(name);
  }

  // 获取所有工具
  getAllTools(): AnyDeclarativeTool[] {
    return Array.from(this.tools.values())
      .sort((a, b) => a.displayName.localeCompare(b.displayName));
  }
}
```

### 3.2 工具基类

✅ **Verified**: `qwen-code/packages/core/src/tools/tools.ts`

```typescript
// 工具调用抽象基类
export abstract class BaseToolInvocation<TParams, TResult> {
  constructor(protected readonly params: TParams) {}

  abstract getDescription(): string;

  abstract execute(
    signal: AbortSignal,
    updateOutput?: (output: ToolResultDisplay) => void,
  ): Promise<TResult>;
}

// 声明式工具基类
export abstract class BaseDeclarativeTool<TParams, TResult> {
  abstract readonly name: string;
  abstract readonly displayName: string;
  abstract readonly description: string;
  abstract readonly kind: Kind;
  abstract readonly schema: FunctionDeclaration;

  protected abstract createInvocation(
    params: TParams,
  ): ToolInvocation<TParams, TResult>;

  // 执行前确认检查
  abstract shouldConfirmExecute(
    params: TParams,
    abortSignal: AbortSignal,
  ): Promise<ToolCallConfirmationDetails | false>;

  // 执行工具
  async execute(
    params: TParams,
    signal: AbortSignal,
    updateOutput?: (output: ToolResultDisplay) => void,
  ): Promise<TResult> {
    const invocation = this.createInvocation(params);
    return invocation.execute(signal, updateOutput);
  }
}
```

### 3.3 CoreToolScheduler

✅ **Verified**: `qwen-code/packages/core/src/core/coreToolScheduler.ts`

```typescript
export class CoreToolScheduler {
  constructor(
    private readonly config: Config,
    private readonly toolRegistry: ToolRegistry,
    private readonly approvalMode: ApprovalMode,
  ) {}

  // 执行待处理的工具调用
  async executePendingCalls(
    pendingCalls: ToolCallRequestInfo[],
    signal: AbortSignal,
  ): Promise<ToolCallResponseInfo[]> {
    const responses: ToolCallResponseInfo[] = [];

    for (const call of pendingCalls) {
      const tool = this.toolRegistry.getTool(call.name);
      if (!tool) {
        responses.push({
          callId: call.callId,
          responseParts: [{
            functionResponse: {
              name: call.name,
              response: { error: `Tool not found: ${call.name}` },
            },
          }],
          resultDisplay: undefined,
          error: new Error(`Tool not found: ${call.name}`),
          errorType: ToolErrorType.TOOL_NOT_FOUND,
        });
        continue;
      }

      // 参数校验
      const validatedParams = this.validateParams(tool, call.args);

      // 用户确认检查
      const confirmation = await tool.shouldConfirmExecute(
        validatedParams,
        signal,
      );

      if (confirmation === false) {
        // 用户拒绝执行
        responses.push({
          callId: call.callId,
          responseParts: [{
            functionResponse: {
              name: call.name,
              response: { error: 'User declined' },
            },
          }],
          resultDisplay: undefined,
          error: new Error('User declined'),
          errorType: ToolErrorType.USER_DECLINED,
        });
        continue;
      }

      // 执行工具
      try {
        const result = await tool.execute(validatedParams, signal);
        responses.push({
          callId: call.callId,
          responseParts: [{
            functionResponse: {
              name: call.name,
              response: { output: result.llmContent },
            },
          }],
          resultDisplay: result.returnDisplay,
          error: result.error ? new Error(result.error.message) : undefined,
          errorType: result.error?.type,
        });
      } catch (error) {
        responses.push({
          callId: call.callId,
          responseParts: [{
            functionResponse: {
              name: call.name,
              response: { error: String(error) },
            },
          }],
          resultDisplay: undefined,
          error: error instanceof Error ? error : new Error(String(error)),
          errorType: ToolErrorType.EXECUTION_ERROR,
        });
      }
    }

    return responses;
  }
}
```

---

## 4. 内置工具

### 4.1 工具清单

| 工具名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| read_file | `tools/read-file.ts` | 读取文件内容 |
| write_file | `tools/write-file.ts` | 写入文件 |
| edit | `tools/edit.ts` | 编辑文件（diff 方式） |
| ls | `tools/ls.ts` | 列出目录内容 |
| grep | `tools/grep.ts` | 文本搜索 |
| glob | `tools/glob.ts` | 文件模式匹配 |
| shell | `tools/shell.ts` | 执行 shell 命令 |
| web_fetch | `tools/web-fetch.ts` | 获取网页内容 |
| memory | `tools/memoryTool.ts` | 记忆存储/检索 |
| todoWrite | `tools/todoWrite.ts` | 待办事项管理 |
| lsp | `tools/lsp.ts` | LSP 集成 |

### 4.2 read-file 示例

✅ **Verified**: `qwen-code/packages/core/src/tools/read-file.ts`

```typescript
export class ReadFileTool extends BaseDeclarativeTool<ReadFileParams, ToolResult> {
  static readonly Name = 'read_file';

  readonly name = ReadFileTool.Name;
  readonly displayName = 'Read File';
  readonly description = 'Read the contents of a file...';
  readonly kind = Kind.Read;

  readonly schema: FunctionDeclaration = {
    name: this.name,
    description: this.description,
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Path to the file' },
        offset: { type: 'number', description: 'Start line (optional)' },
        limit: { type: 'number', description: 'Max lines to read (optional)' },
      },
      required: ['path'],
    },
  };

  protected createInvocation(params: ReadFileParams): ToolInvocation<ReadFileParams, ToolResult> {
    return new ReadFileInvocation(this.config, params);
  }

  async shouldConfirmExecute(
    params: ReadFileParams,
    abortSignal: AbortSignal,
  ): Promise<ToolCallConfirmationDetails | false> {
    // read_file 通常不需要确认
    return false;
  }
}

class ReadFileInvocation extends BaseToolInvocation<ReadFileParams, ToolResult> {
  async execute(signal: AbortSignal): Promise<ToolResult> {
    const { path, offset, limit } = this.params;
    const content = await fs.readFile(path, 'utf-8');

    // 处理 offset/limit
    const lines = content.split('\n');
    const start = offset || 0;
    const end = limit ? start + limit : lines.length;
    const selectedLines = lines.slice(start, end);

    return {
      llmContent: selectedLines.join('\n'),
      returnDisplay: selectedLines.join('\n'),
    };
  }
}
```

---

## 5. 发现工具

### 5.1 命令发现

通过配置 `toolDiscoveryCommand` 从外部命令动态发现工具：

```typescript
// 配置示例（settings.json）
{
  "tools": {
    "toolDiscoveryCommand": "./scripts/discover-tools.sh"
  }
}

// 发现命令输出格式（JSON 数组）
[
  {
    "name": "custom_tool",
    "description": "A custom tool",
    "parameters": {
      "type": "object",
      "properties": {
        "arg1": { "type": "string" }
      },
      "required": ["arg1"]
    }
  }
]
```

### 5.2 DiscoveredTool 实现

✅ **Verified**: `qwen-code/packages/core/src/tools/tool-registry.ts:32`

```typescript
class DiscoveredTool extends BaseDeclarativeTool<ToolParams, ToolResult> {
  constructor(
    private readonly config: Config,
    name: string,
    description: string,
    parameterSchema: Record<string, unknown>,
  ) {
    super(name, name, description, Kind.Other, parameterSchema, false, false);
  }

  protected createInvocation(params: ToolParams): ToolInvocation<ToolParams, ToolResult> {
    return new DiscoveredToolInvocation(this.config, this.name, params);
  }
}

class DiscoveredToolInvocation extends BaseToolInvocation<ToolParams, ToolResult> {
  async execute(): Promise<ToolResult> {
    const callCommand = this.config.getToolCallCommand()!;
    const child = spawn(callCommand, [this.toolName]);
    child.stdin.write(JSON.stringify(this.params));
    child.stdin.end();

    // 收集 stdout/stderr
    let stdout = '';
    let stderr = '';
    // ... 处理输出 ...

    if (code !== 0) {
      return {
        llmContent: `Error: ${stderr}`,
        returnDisplay: stderr,
        error: { message: stderr, type: ToolErrorType.DISCOVERED_TOOL_EXECUTION_ERROR },
      };
    }

    return { llmContent: stdout, returnDisplay: stdout };
  }
}
```

---

## 6. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 工具未注册 | 检查 discoverAllTools 调用 | `tool-registry.ts:237` |
| 工具名冲突 | 检查 MCP 完全限定名 | `tool-registry.ts:200` |
| 参数校验失败 | 检查工具 schema 定义 | 各工具文件 |
| 确认对话框不弹出 | 检查 shouldConfirmExecute | `coreToolScheduler.ts` |
| 发现工具执行失败 | 检查 toolCallCommand | `tool-registry.ts:52` |
| MCP 工具不显示 | 检查 MCP 连接状态 | `mcp-client-manager.ts` |

---

## 7. 架构特点

### 7.1 三层工具分类

```
1. 内置工具 (Built-in)
   - 代码维护在源码中
   - 类型安全
   - 开箱即用

2. MCP 工具 (DiscoveredMCPTool)
   - 动态发现自 MCP 服务器
   - 完全限定名避免冲突
   - 支持 prompts/resources

3. 发现工具 (DiscoveredTool)
   - 通过外部命令发现
   - 子进程执行
   - 适用于遗留系统集成
```

### 7.2 冲突处理

```typescript
// MCP 工具使用完全限定名：serverName__toolName
if (tool instanceof DiscoveredMCPTool) {
  tool = tool.asFullyQualifiedTool();  // serverName__toolName
} else {
  // 其他工具：覆盖警告
  debugLogger.warn(`Tool "${tool.name}" already registered. Overwriting.`);
}
```

---

## 8. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 内置工具 | 丰富 | ✅ 继承 |
| MCP 支持 | ✅ 支持 | ✅ 继承 |
| 命令发现 | ✅ 支持 | ✅ 继承 |
| 确认机制 | ApprovalMode | ✅ 继承 |
| 工具分类 | Kind 枚举 | ✅ 继承 |

---

## 9. 总结

Qwen Code 的 Tools 系统特点：

1. **三层架构** - 内置、MCP、发现工具满足不同场景
2. **类型安全** - TypeScript 泛型保障参数和返回值
3. **确认机制** - 灵活的 shouldConfirmExecute 控制
4. **冲突处理** - MCP 完全限定名避免命名冲突
5. **可扩展** - 通过 MCP 或命令发现轻松扩展
