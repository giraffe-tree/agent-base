# MCP 集成（gemini-cli）

本文基于 `./gemini-cli` 源码，解释 Gemini CLI 如何实现 MCP (Model Context Protocol) 接入，支持多传输、动态工具发现和完整的 OAuth 2.0 认证。

---

## 1. 先看全局（流程图）

### 1.1 MCP 工具发现与调用流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  配置加载                                                       │
│  ┌────────────────────────────────────────┐                     │
│  │ settings.json / gemini.exe.yml         │                     │
│  │  └── mcpServers:                       │                     │
│  │      server1: {command, args, env}     │                     │
│  │      server2: {url, oauth}             │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  MCP Client Manager 初始化                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ McpClientManager::discoverAll()        │                     │
│  │  ├── 遍历所有 mcpServers 配置          │                     │
│  │  ├── maybeDiscoverMcpServer()          │                     │
│  │  │   └── McpClient::connect()          │                     │
│  │  │       └── connectToMcpServer()      │                     │
│  │  │           ├── createTransport()     │                     │
│  │  │           │   ├── StreamableHTTP    │                     │
│  │  │           │   ├── SSE               │                     │
│  │  │           │   └── Stdio             │                     │
│  │  │           └── client.connect()      │                     │
│  │  └── McpClient::discover()             │                     │
│  │       ├── fetchPrompts()               │                     │
│  │       ├── discoverTools()              │                     │
│  │       └── discoverResources()          │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  工具注册到 Registry                                             │
│  ┌────────────────────────────────────────┐                     │
│  │ DiscoveredMCPTool                      │                     │
│  │  └── 工具名: {server}__{tool}           │                     │
│  │  └── 包装为 CallableTool               │                     │
│  │       └── callTool() -> Part[]         │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件架构图

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Gemini CLI 核心层                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              McpClientManager                            │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │  McpClient  │  │  McpClient  │  │      ...        │  │   │
│  │  │  (server1)  │  │  (server2)  │  │                 │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────────┘  │   │
│  └─────────┼────────────────┼───────────────────────────────┘   │
└────────────┼────────────────┼────────────────────────────────────┘
             │                │
             ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Tool / Prompt / Resource                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ DiscoveredMCPTool│  │DiscoveredMCPPrompt│  │   Resource      │  │
│  │  (CallableTool) │  │                 │  │                 │  │
│  │  server__tool   │  │  server/prompt  │  │  uri -> content │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Policy Engine                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  审批决策：always_require / auto / trust                  │   │
│  │  支持通配符："google-workspace__*"                        │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `3.1`（知道工具名格式 `server__tool` 和多传输支持）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道架构、传输层设计、OAuth 实现）。
- **10 分钟版**：通读全文（能配置、调试 MCP 服务器，处理认证问题）。

### 2.1 一句话定义

Gemini CLI 的 MCP 集成采用"**多传输自动回退 + 动态发现 + Policy Engine 审批**"的设计：支持 StreamableHTTP/SSE/Stdio/WebSocket 多种传输，运行时动态发现工具/Prompt/Resource，并通过 Policy Engine 实现细粒度的工具调用审批。

---

## 3. 核心组件详解

### 3.1 配置结构

**文件**: `packages/core/src/config/config.ts:314-400`

```typescript
export class MCPServerConfig {
  constructor(
    // Stdio 传输配置
    readonly command?: string,
    readonly args?: string[],
    readonly env?: Record<string, string>,
    readonly cwd?: string,
    // HTTP/SSE 传输配置
    readonly url?: string,
    readonly httpUrl?: string,
    readonly headers?: Record<string, string>,
    // 传输类型: 'sse' | 'http' | 'ws' | undefined
    readonly type?: 'sse' | 'http' | 'ws',
    // 通用配置
    readonly timeout?: number,      // 默认 10 分钟
    readonly trust?: boolean,       // 是否信任该服务器
    // OAuth 配置
    readonly oauth?: MCPOAuthConfig,
    readonly authProviderType?: AuthProviderType,
  ) {}
}

type MCPOAuthConfig = {
  clientName?: string;
  clientUri?: string;
  logoUri?: string;
  tosUri?: string;
  policyUri?: string;
  scopes?: string[];
};
```

**配置示例** (settings.json):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "url": "https://api.github.com/mcp",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}"
      }
    },
    "google-workspace": {
      "httpUrl": "https://mcp-gdrive.googleapis.com",
      "oauth": {
        "clientName": "Gemini CLI",
        "scopes": ["drive.readonly"]
      }
    }
  }
}
```

### 3.2 传输层设计与自动回退

**文件**: `packages/core/src/tools/mcp-client.ts`

Gemini CLI 支持多种传输方式，并实现了智能回退机制：

```typescript
async function createTransport(
  mcpServerName: string,
  mcpServerConfig: MCPServerConfig,
  debugMode: boolean,
  sanitizationConfig: EnvironmentSanitizationConfig,
): Promise<Transport> {
  // 1. HTTP/SSE 传输（远程服务器）
  if (mcpServerConfig.httpUrl || mcpServerConfig.url) {
    const authProvider = createAuthProvider(mcpServerConfig);
    const headers = await authProvider?.getRequestHeaders?.() ?? {};

    // 尝试多种传输方式
    const transports = [
      {
        name: 'StreamableHTTP',
        transport: new StreamableHTTPClientTransport(url, { authProvider }),
      },
      {
        name: 'SSE',
        transport: new SSEClientTransport(url, { authProvider }),
      },
      {
        name: 'WebSocket',
        transport: new WebSocketClientTransport(url, { authProvider }),
      },
    ];

    // 依次尝试直到成功
    for (const { name, transport } of transports) {
      try {
        return await tryConnect(transport);
      } catch (error) {
        debugLogger.log(`${name} failed, trying next...`);
      }
    }
  }

  // 2. Stdio 传输（本地服务器）
  if (mcpServerConfig.command) {
    return new StdioClientTransport({
      command: mcpServerConfig.command,
      args: mcpServerConfig.args || [],
      env: sanitizedEnv,
    });
  }
}
```

**传输优先级**:
1. StreamableHTTP（推荐，支持 OAuth）
2. SSE（兼容旧服务器）
3. WebSocket（特定场景）
4. Stdio（本地进程）

### 3.3 MCP Client Manager

**文件**: `packages/core/src/tools/mcp-client-manager.ts`

`McpClientManager` 是 MCP 集成的核心管理者：

```typescript
export class McpClientManager {
  private clients: Map<string, McpClient> = new Map();
  private allServerConfigs: Map<string, MCPServerConfig> = new Map();
  private discoveryState: MCPDiscoveryState = MCPDiscoveryState.NOT_STARTED;

  constructor(
    private readonly toolRegistry: ToolRegistry,
    private readonly cliConfig: Config,
    private readonly eventEmitter?: EventEmitter,
  ) {}

  // 启动扩展的 MCP 服务器
  async startExtension(extension: GeminiCLIExtension) {
    await Promise.all(
      Object.entries(extension.mcpServers ?? {}).map(([name, config]) =>
        this.maybeDiscoverMcpServer(name, { ...config, extension }),
      ),
    );
  }

  // 停止扩展的 MCP 服务器
  async stopExtension(extension: GeminiCLIExtension) {
    await Promise.all(
      Object.keys(extension.mcpServers ?? {}).map((name) =>
        this.disconnectClient(name, true),
      ),
    );
  }

  // 检查是否被管理员设置阻止
  private isBlockedBySettings(name: string): boolean {
    const allowedNames = this.cliConfig.getAllowedMcpServers();
    if (allowedNames && !allowedNames.includes(name)) return true;

    const blockedNames = this.cliConfig.getBlockedMcpServers();
    if (blockedNames?.includes(name)) return true;

    return false;
  }
}
```

### 3.4 工具发现与注册

**文件**: `packages/core/src/tools/mcp-client.ts:180-250`

```typescript
export class McpClient {
  async discover(cliConfig: Config): Promise<void> {
    const prompts = await this.fetchPrompts();
    const tools = await this.discoverTools(cliConfig);
    const resources = await this.discoverResources();

    // 注册到各自的 Registry
    for (const prompt of prompts) {
      this.promptRegistry.registerPrompt(prompt);
    }
    for (const tool of tools) {
      this.toolRegistry.registerTool(tool);
    }
    this.updateResourceRegistry(resources);
  }

  private async discoverTools(cliConfig: Config): Promise<DiscoveredMCPTool[]> {
    const response = await this.client.listTools({}, { timeout });
    const discoveredTools: DiscoveredMCPTool[] = [];

    for (const toolDef of response.tools) {
      const mcpCallableTool = new McpCallableTool(
        this.client,
        toolDef,
        this.serverConfig.timeout ?? MCP_DEFAULT_TIMEOUT_MSEC,
      );

      const tool = new DiscoveredMCPTool(
        mcpCallableTool,
        this.serverName,
        toolDef.name,
        toolDef.description ?? '',
        toolDef.inputSchema ?? { type: 'object', properties: {} },
        this.messageBus,
        this.serverConfig.trust,  // 是否信任该服务器
        isReadOnly,               // 是否为只读工具
      );
      discoveredTools.push(tool);
    }

    return discoveredTools;
  }
}
```

### 3.5 工具命名与 Policy Engine

**文件**: `packages/core/src/tools/mcp-tool.ts:26-95`

```typescript
export const MCP_QUALIFIED_NAME_SEPARATOR = '__';

export class DiscoveredMCPToolInvocation extends BaseToolInvocation {
  constructor(
    private readonly mcpTool: CallableTool,
    readonly serverName: string,
    readonly serverToolName: string,
    messageBus: MessageBus,
    readonly trust?: boolean,
  ) {
    // 使用组合格式进行策略检查: serverName__toolName
    // 支持服务器通配符 (e.g., "google-workspace__*")
    super(
      params,
      messageBus,
      `${serverName}${MCP_QUALIFIED_NAME_SEPARATOR}${serverToolName}`,
      displayName,
      serverName,
    );
  }
}
```

**Policy 配置示例**:

```yaml
# 始终需要审批
tools:
  - pattern: "filesystem__write_file"
    mode: always_require

# 信任的服务器自动批准
  - pattern: "google-workspace__*"
    mode: trust

# 只读工具自动批准
  - pattern: "*__read_*"
    mode: auto
```

### 3.6 动态刷新机制

**文件**: `packages/core/src/tools/mcp-client.ts:118-140`

MCP 服务器可以在运行时通知客户端工具列表的变化：

```typescript
private registerNotificationHandlers() {
  // 工具列表变化通知
  this.client.setNotificationHandler(
    ToolListChangedNotificationSchema,
    async () => {
      if (this.isRefreshingTools) {
        this.pendingToolRefresh = true;
        return;
      }
      await this.refreshToolsWithCoalescing();
    },
  );

  // Resource 列表变化通知
  this.client.setNotificationHandler(
    ResourceListChangedNotificationSchema,
    async () => { ... },
  );

  // Prompt 列表变化通知
  this.client.setNotificationHandler(
    PromptListChangedNotificationSchema,
    async () => { ... },
  );
}
```

---

## 4. OAuth 2.0 实现

### 4.1 OAuth Provider 架构

**文件**: `packages/core/src/mcp/oauth-provider.ts`

```typescript
export class MCPOAuthProvider implements McpAuthProvider {
  async getRequestHeaders(): Promise<Record<string, string>> {
    const token = await this.getAccessToken();
    return { Authorization: `Bearer ${token}` };
  }

  async getAccessToken(): Promise<string> {
    // 1. 检查现有 token
    const storedToken = await this.tokenStorage.getToken();
    if (storedToken && !this.isExpired(storedToken)) {
      return storedToken.access_token;
    }

    // 2. 自动刷新
    if (storedToken?.refresh_token) {
      return this.refreshToken(storedToken.refresh_token);
    }

    // 3. 启动 OAuth 流程
    return this.startOAuthFlow();
  }

  private async startOAuthFlow(): Promise<string> {
    // 支持 RFC 8414 OAuth 自动发现
    const metadata = await this.discoverOAuthMetadata();
    // 动态客户端注册
    const clientRegistration = await this.registerClient(metadata);
    // 授权码流程
    const code = await this.startAuthorizationFlow(metadata, clientRegistration);
    // 交换 token
    return this.exchangeCodeForToken(metadata, clientRegistration, code);
  }
}
```

### 4.2 Google 认证支持

**文件**: `packages/core/src/mcp/google-auth-provider.ts`

支持 Google 服务账号和身份模拟：

```typescript
export class GoogleCredentialProvider implements McpAuthProvider {
  async getRequestHeaders(): Promise<Record<string, string>> {
    const auth = new GoogleAuth({
      scopes: this.scopes,
      keyFile: this.serviceAccountKeyFile,
    });
    const token = await auth.getAccessToken();
    return { Authorization: `Bearer ${token}` };
  }
}
```

---

## 5. Prompt 与 Resource 支持

除了工具调用，Gemini CLI 还支持 MCP 的 Prompt 和 Resource 功能：

### 5.1 Prompt 注册

```typescript
// 从 MCP 服务器获取 prompts
const prompts = await this.fetchPrompts();
for (const prompt of prompts) {
  this.promptRegistry.registerPrompt({
    ...prompt,
    serverName: this.serverName,
    invoke: async (params) => {
      return this.client.getPrompt({ name: prompt.name, arguments: params });
    },
  });
}
```

### 5.2 Resource 注册

```typescript
// 从 MCP 服务器获取 resources
const resources = await this.discoverResources();
for (const resource of resources) {
  this.resourceRegistry.registerResource({
    uri: resource.uri,
    name: resource.name,
    read: async () => {
      return this.client.readResource({ uri: resource.uri });
    },
  });
}
```

---

## 6. 与 Agent Loop 的集成

MCP 工具如何融入 Gemini CLI 的 Agent Loop：

1. **启动时**: `Config` 初始化 `McpClientManager`，发现所有 MCP 工具
2. **工具调度**: `CoreToolScheduler` 从 `ToolRegistry` 获取工具列表
3. **工具调用**: 模型输出 → `DiscoveredMCPToolInvocation` → `McpCallableTool.callTool()`
4. **审批检查**: `Policy Engine` 根据 `trust` 和配置决定是否拦截
5. **结果返回**: MCP 结果转换为 Gemini `Part` 格式，返回给模型

---

## 7. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| 连接失败 | 检查传输类型和 URL | `mcp-client.ts:createTransport()` |
| OAuth 认证失败 | 检查 discovery URL 和 client registration | `oauth-provider.ts` |
| 工具不显示 | 检查 `isBlockedBySettings()` 允许列表 | `mcp-client-manager.ts:118` |
| 调用超时 | 检查 `timeout` 配置，默认 10 分钟 | `mcp-client.ts:75` |
| 审批问题 | 检查 Policy 配置和 `trust` 标志 | `mcp-tool.ts` |

---

## 8. 架构特点总结

- **多传输支持**: StreamableHTTP / SSE / WebSocket / Stdio，自动回退
- **完整 OAuth 2.0**: 自动发现、动态注册、自动刷新
- **Google 集成**: 原生支持 Google 服务账号和身份模拟
- **动态发现**: 运行时工具/Prompt/Resource 发现，支持实时刷新
- **Policy Engine**: 细粒度审批控制，支持服务器级通配符
- **扩展集成**: 支持通过 Extension 机制加载 MCP 服务器
