# MCP 集成（Qwen Code）

本文分析 Qwen Code 的 MCP（Model Context Protocol）集成机制，包括 MCP 客户端管理、传输层支持和工具发现。

---

## 1. 先看全局（流程图）

### 1.1 MCP 架构概览

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        Qwen Code                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    ToolRegistry                             │   │
│  │                     (工具注册表)                             │   │
│  └────────────────────┬────────────────────────────────────────┘   │
│                       │                                             │
│                       ▼                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                McpClientManager                             │   │
│  │          (packages/core/src/tools/                          │   │
│  │           mcp-client-manager.ts:29)                         │   │
│  │  ┌───────────────────────────────────────────────────────┐  │   │
│  │  │ clients: Map<string, McpClient>                       │  │   │
│  │  │                                                       │  │   │
│  │  │ discoverAllMcpTools()  ──► 并行发现所有服务器工具      │  │   │
│  │  │ discoverMcpToolsForServer() ──► 单服务器发现           │  │   │
│  │  │ restartMcpServers()    ──► 重启所有服务器             │  │   │
│  │  │ readResource()         ──► 读取 MCP 资源              │  │   │
│  │  └───────────────────────────────────────────────────────┘  │   │
│  └────────────────────┬────────────────────────────────────────┘   │
│                       │                                             │
│                       ▼                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   McpClient (每个服务器一个)                  │   │
│  │          (packages/core/src/tools/                          │   │
│  │           mcp-client.ts)                                    │   │
│  │  ┌───────────────────────────────────────────────────────┐  │   │
│  │  │ connect()    ──► 建立传输层连接                        │  │   │
│  │  │ discover()   ──► tools/list + prompts/list            │  │   │
│  │  │ callTool()   ──► tools/call                           │  │   │
│  │  │ getPrompt()  ──► prompts/get                          │  │   │
│  │  │ readResource() ──► resources/read                     │  │   │
│  │  └───────────────────────────────────────────────────────┘  │   │
│  └────────────────────┬────────────────────────────────────────┘   │
│                       │                                             │
└───────────────────────┼─────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      传输层                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │   stdio     │  │    SSE      │  │   HTTP      │  │ InMemory    │ │
│  │  (子进程)    │  │(Server-Sent │  │  (REST)     │  │  (SDK)      │ │
│  │             │  │  Events)    │  │             │  │             │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   MCP Servers (外部进程)                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │ Filesystem  │  │   GitHub    │  │   Custom    │  ...             │
│  │   Server    │  │   Server    │  │   Server    │                  │
│  └─────────────┘  └─────────────┘  └─────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘

图例: MCP = Model Context Protocol，标准化工具/资源/prompt 交互协议
```

### 1.2 MCP 发现流程

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     MCP 工具发现时序                                  │
└──────────────────────────────────────────────────────────────────────┘

Application Start
       │
       ▼
┌───────────────┐
│ loadCliConfig │
└───────┬───────┘
       │
       ▼
┌─────────────────────┐
│ discoverAllTools()  │
└────────┬────────────┘
         │
         ▼
┌─────────────────────────┐
│ mcpClientManager.       │
│ discoverAllMcpTools()   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ 1. Stop existing clients│
│ 2. Load MCP configs     │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│ Parallel Discovery      │────►│ For each MCP server:    │
└─────────────────────────┘     │  1. Create McpClient    │
                                │  2. client.connect()    │
                                │  3. client.discover()   │
                                │     - tools/list        │
                                │     - prompts/list      │
                                │  4. Register tools      │
                                │     with ToolRegistry   │
                                └─────────────────────────┘
                                         │
                                         ▼
                                ┌─────────────────────────┐
                                │ Event: mcp-client-update│
                                │ (用于 UI 状态显示)       │
                                └─────────────────────────┘
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道 McpClientManager 管理多个 McpClient，支持 stdio/SSE/HTTP 传输层。
- **3 分钟版**：看 `1.1` + `1.2` + `3.1` 节，了解发现流程和传输层实现。
- **10 分钟版**：通读全文，掌握 MCP 配置、OAuth、错误处理。

### 2.1 一句话定义

Qwen Code 的 MCP 集成是「**管理器-客户端-传输**」三层架构：McpClientManager 统一管理多个 McpClient，每个 McpClient 通过不同传输层（stdio/SSE/HTTP）连接外部 MCP 服务器，动态发现工具/资源/prompts。

---

## 3. 核心组件

### 3.1 McpClientManager

✅ **Verified**: `qwen-code/packages/core/src/tools/mcp-client-manager.ts:29`

```typescript
export class McpClientManager {
  private clients: Map<string, McpClient> = new Map();
  private discoveryState: MCPDiscoveryState = MCPDiscoveryState.NOT_STARTED;

  constructor(
    config: Config,
    toolRegistry: ToolRegistry,
    eventEmitter?: EventEmitter,
    sendSdkMcpMessage?: SendSdkMcpMessage,
  ) {
    this.cliConfig = config;
    this.toolRegistry = toolRegistry;
    this.eventEmitter = eventEmitter;
    this.sendSdkMcpMessage = sendSdkMcpMessage;
  }

  // 发现所有 MCP 服务器的工具
  async discoverAllMcpTools(cliConfig: Config): Promise<void> {
    if (!cliConfig.isTrustedFolder()) {
      return;  // 不受信任文件夹不启用 MCP
    }

    await this.stop();  // 先停止现有连接

    const servers = populateMcpServerCommand(
      this.cliConfig.getMcpServers() || {},
      this.cliConfig.getMcpServerCommand(),
    );

    this.discoveryState = MCPDiscoveryState.IN_PROGRESS;
    this.eventEmitter?.emit('mcp-client-update', this.clients);

    // 并行发现所有服务器
    const discoveryPromises = Object.entries(servers).map(
      async ([name, config]) => {
        const sdkCallback = isSdkMcpServerConfig(config)
          ? this.sendSdkMcpMessage
          : undefined;

        const client = new McpClient(
          name,
          config,
          this.toolRegistry,
          this.cliConfig.getPromptRegistry(),
          this.cliConfig.getWorkspaceContext(),
          this.cliConfig.getDebugMode(),
          sdkCallback,
        );
        this.clients.set(name, client);
        this.eventEmitter?.emit('mcp-client-update', this.clients);

        try {
          await client.connect();
          await client.discover(cliConfig);
          this.eventEmitter?.emit('mcp-client-update', this.clients);
        } catch (error) {
          debugLogger.error(`MCP discovery failed for '${name}':`, error);
          this.eventEmitter?.emit('mcp-client-update', this.clients);
        }
      },
    );

    await Promise.all(discoveryPromises);
    this.discoveryState = MCPDiscoveryState.COMPLETED;
  }

  // 读取 MCP 资源
  async readResource(
    serverName: string,
    uri: string,
    options?: { signal?: AbortSignal },
  ): Promise<ReadResourceResult> {
    const client = this.clients.get(serverName);
    if (!client) {
      throw new Error(`MCP server '${serverName}' not found`);
    }
    return client.readResource(uri, options);
  }

  // 停止所有客户端
  async stop(): Promise<void> {
    for (const [name, client] of this.clients) {
      try {
        await client.disconnect();
      } catch (error) {
        debugLogger.error(`Error stopping client '${name}':`, error);
      }
    }
    this.clients.clear();
    this.discoveryState = MCPDiscoveryState.NOT_STARTED;
  }
}
```

### 3.2 McpClient

✅ **Verified**: `qwen-code/packages/core/src/tools/mcp-client.ts`

```typescript
export class McpClient {
  private client?: Client;
  private transport?: Transport;
  private serverStatus: MCPServerStatus = MCPServerStatus.DISCONNECTED;
  private discoveryState: MCPDiscoveryState = MCPDiscoveryState.NOT_STARTED;
  private tools: DiscoveredMCPTool[] = [];
  private prompts: DiscoveredMCPPrompt[] = [];

  constructor(
    private readonly serverName: string,
    private readonly config: MCP_SERVER_CONFIG_TYPE,
    private readonly toolRegistry: ToolRegistry,
    private readonly promptRegistry: PromptRegistry,
    private readonly workspaceContext: string,
    private readonly debugMode: boolean,
    private readonly sendSdkMcpMessage?: SendSdkMcpMessage,
  ) {}

  // 建立连接
  async connect(): Promise<void> {
    const transport = this.createTransport();
    this.client = new Client({ name: 'qwen-code', version: getVersion() });

    await this.client.connect(transport);
    this.serverStatus = MCPServerStatus.CONNECTED;
  }

  // 发现工具和资源
  async discover(cliConfig: Config): Promise<void> {
    if (!this.client) throw new Error('Not connected');

    this.discoveryState = MCPDiscoveryState.IN_PROGRESS;

    try {
      // 发现工具
      const toolsResult = await this.client.listTools();
      for (const tool of toolsResult.tools || []) {
        const discoveredTool = new DiscoveredMCPTool(
          this.serverName,
          tool,
          this,
          cliConfig,
        );
        this.tools.push(discoveredTool);
        this.toolRegistry.registerTool(discoveredTool);
      }

      // 发现 prompts
      try {
        const promptsResult = await this.client.listPrompts();
        for (const prompt of promptsResult.prompts || []) {
          const discoveredPrompt = new DiscoveredMCPPrompt(
            this.serverName,
            prompt,
            this,
          );
          this.prompts.push(discoveredPrompt);
          this.promptRegistry.registerPrompt(discoveredPrompt);
        }
      } catch {
        // 服务器可能不支持 prompts
      }

      this.discoveryState = MCPDiscoveryState.COMPLETED;
    } catch (error) {
      this.discoveryState = MCPDiscoveryState.FAILED;
      throw error;
    }
  }

  // 调用工具
  async callTool(
    toolName: string,
    args: Record<string, unknown>,
    options?: { signal?: AbortSignal },
  ): Promise<CallToolResult> {
    if (!this.client) throw new Error('Not connected');

    const result = await this.client.callTool(
      { name: toolName, arguments: args },
      undefined,
      { signal: options?.signal },
    );

    return result;
  }

  // 创建传输层
  private createTransport(): Transport {
    if (this.config.transport === 'sse') {
      return new SSEClientTransport(new URL(this.config.url));
    } else if (this.config.transport === 'http') {
      return new HTTPClientTransport(new URL(this.config.url));
    } else if (isSdkMcpServerConfig(this.config)) {
      // SDK 模式使用内存传输
      return new InMemoryTransport(this.sendSdkMcpMessage!);
    } else {
      // 默认 stdio
      return new StdioClientTransport({
        command: this.config.command,
        args: this.config.args,
        env: { ...process.env, ...this.config.env },
      });
    }
  }
}
```

### 3.3 DiscoveredMCPTool

✅ **Verified**: `qwen-code/packages/core/src/tools/mcp-tool.ts`

```typescript
export class DiscoveredMCPTool extends BaseDeclarativeTool<
  Record<string, unknown>,
  ToolResult
> {
  constructor(
    readonly serverName: string,
    private readonly toolInfo: Tool,
    private readonly mcpClient: McpClient,
    private readonly config: Config,
  ) {
    super(
      toolInfo.name,
      toolInfo.name,
      toolInfo.description || '',
      Kind.Other,
      toolInfo.inputSchema || {},
      false,
      false,
    );
  }

  // 转换为完全限定名（避免冲突）
  asFullyQualifiedTool(): DiscoveredMCPTool {
    return new DiscoveredMCPTool(
      this.serverName,
      { ...this.toolInfo, name: `${this.serverName}__${this.name}` },
      this.mcpClient,
      this.config,
    );
  }

  protected createInvocation(
    params: Record<string, unknown>,
  ): ToolInvocation<Record<string, unknown>, ToolResult> {
    return new McpToolInvocation(this, params, this.mcpClient, this.config);
  }

  async shouldConfirmExecute(
    params: Record<string, unknown>,
    abortSignal: AbortSignal,
  ): Promise<ToolCallConfirmationDetails | false> {
    // MCP 工具默认需要确认
    return {
      toolName: this.displayName,
      description: this.getDescription(params),
    };
  }
}

class McpToolInvocation extends BaseToolInvocation<
  Record<string, unknown>,
  ToolResult
> {
  async execute(signal: AbortSignal): Promise<ToolResult> {
    const result = await this.mcpClient.callTool(
      this.tool.name,
      this.params,
      { signal },
    );

    // 处理工具结果
    const content = result.content || [];
    const textContent = content
      .filter((c): c is TextContent => c.type === 'text')
      .map((c) => c.text)
      .join('\n');

    return {
      llmContent: textContent,
      returnDisplay: textContent,
    };
  }
}
```

---

## 4. MCP 配置

### 4.1 配置格式

```json
// .qwen/settings.json 或 ~/.qwen/settings.json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/workspace"],
      "env": {
        "NODE_ENV": "production"
      }
    },
    "github": {
      "transport": "sse",
      "url": "https://api.github.com/mcp/sse"
    },
    "custom-http": {
      "transport": "http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

### 4.2 传输层类型

| 传输层 | 配置 | 适用场景 |
|--------|------|----------|
| stdio | `command` + `args` | 本地子进程 |
| SSE | `transport: 'sse'` + `url` | 远程服务器推送 |
| HTTP | `transport: 'http'` + `url` | 远程 REST API |
| InMemory | SDK 模式 | 嵌入式使用 |

---

## 5. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| MCP 工具未显示 | 检查 isTrustedFolder | `mcp-client-manager.ts:56` |
| 连接失败 | 检查传输层配置 | `mcp-client.ts` |
| 发现超时 | 检查 discoveryState | `mcp-client.ts` |
| 工具调用失败 | 检查 callTool 参数 | `mcp-client.ts` |
| 命名冲突 | 检查 asFullyQualifiedTool | `mcp-tool.ts` |
| 资源读取失败 | 检查 readResource | `mcp-client-manager.ts:479` |

---

## 6. 架构特点

### 6.1 安全考虑

```typescript
// 1. 不受信任文件夹禁用 MCP
if (!cliConfig.isTrustedFolder()) {
  return;  // 不发现 MCP 工具
}

// 2. 完全限定名避免冲突
tool.asFullyQualifiedTool();  // serverName__toolName

// 3. 清理时断开连接
async stop(): Promise<void> {
  for (const client of this.clients.values()) {
    await client.disconnect();
  }
}
```

### 6.2 并发发现

```typescript
// 并行发现所有服务器，提高启动速度
const discoveryPromises = Object.entries(servers).map(
  async ([name, config]) => {
    const client = new McpClient(...);
    this.clients.set(name, client);
    await client.connect();
    await client.discover(cliConfig);
  },
);
await Promise.all(discoveryPromises);
```

---

## 7. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| MCP 支持 | ✅ 完整 | ✅ 继承 |
| 传输层 | stdio/SSE | ✅ 继承 |
| 工具发现 | 自动 | ✅ 继承 |
| Prompts 发现 | 支持 | ✅ 继承 |
| 资源读取 | 支持 | ✅ 继承 |
| 安全限制 | 受信任文件夹 | ✅ 继承 |

---

## 8. 总结

Qwen Code 的 MCP 集成特点：

1. **管理器模式** - McpClientManager 统一生命周期管理
2. **多传输层** - stdio/SSE/HTTP/InMemory 灵活适配
3. **并发发现** - 并行连接多个 MCP 服务器
4. **安全默认** - 受信任文件夹限制，完全限定名防冲突
5. **事件驱动** - mcp-client-update 事件支持 UI 状态同步
