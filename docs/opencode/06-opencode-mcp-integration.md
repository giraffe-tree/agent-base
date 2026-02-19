# MCP 集成（opencode）

本文基于 `./opencode` 源码，解释 OpenCode 如何实现 MCP (Model Context Protocol) 接入，重点介绍其与 Vercel AI SDK 的深度集成。

---

## 1. 先看全局（流程图）

### 1.1 MCP 工具集成到 AI SDK 的流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  配置加载（多层级）                                              │
│  ┌────────────────────────────────────────┐                     │
│  │ 1) Remote .well-known/opencode         │                     │
│  │ 2) Global config (~/.config/...)       │                     │
│  │ 3) Project config (opencode.json)      │                     │
│  │                                        │                     │
│  │  └── mcp: {                            │                     │
│  │        server1: {type: "local", ...}   │                     │
│  │        server2: {type: "remote", ...}  │                     │
│  │      }                                 │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  MCP 客户端初始化                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ Instance.state() 初始化                │                     │
│  │  └── 遍历所有 MCP 配置                 │                     │
│  │       └── create(key, mcp)             │                     │
│  │            ├── 本地: StdioClientTransport                     │
│  │            └── 远程: StreamableHTTP → SSE (fallback)          │
│  │                 └── McpOAuthProvider                        │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  工具转换为 AI SDK 格式                                         │
│  ┌────────────────────────────────────────┐                     │
│  │ convertMcpTool(mcpTool, client)        │                     │
│  │  └── dynamicTool({                     │                     │
│  │        description: ...,               │                     │
│  │        inputSchema: jsonSchema(...),   │                     │
│  │        execute: (args) => client.callTool(...)                │
│  │      })                                │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Agent Loop 中使用                                              │
│  ┌────────────────────────────────────────┐                     │
│  │ generateText({ tools: [mcpTools] })    │                     │
│  │  └── 模型调用 MCP 工具                 │                     │
│  │       └── execute() 调用远程 MCP 服务器│                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件架构图

```text
┌─────────────────────────────────────────────────────────────────┐
│                    Vercel AI SDK 层                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              generateText / generateObject               │   │
│  │  ┌─────────────────────────────────────────────────┐   │   │
│  │  │  tools: [dynamicTool, dynamicTool, ...]         │   │   │
│  │  │       ↑ 这些是通过 convertMcpTool 创建的        │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      OpenCode MCP 层                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  packages/opencode/src/mcp/index.ts                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │   │
│  │  │    Client    │  │    Client    │  │     ...        │ │   │
│  │  │   (local)    │  │   (remote)   │  │                │ │   │
│  │  │  stdio       │  │  http/sse    │  │                │ │   │
│  │  └──────┬───────┘  └──────┬───────┘  └────────────────┘ │   │
│  │         └─────────────────┘                             │   │
│  │                    │                                    │   │
│  │         ┌──────────┴──────────┐                         │   │
│  │         ▼                     ▼                         │   │
│  │  ┌──────────────┐    ┌──────────────────┐              │   │
│  │  │ McpOAuthProvider│  │ Instance.state() │              │   │
│  │  │              │    │   (状态管理)      │              │   │
│  │  └──────────────┘    └──────────────────┘              │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MCP SDK 传输层                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ StdioClientTransport              │  │ StreamableHTTPClientTransport  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `3.1`（知道 AI SDK `dynamicTool` 转换和配置结构）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道架构、状态机、OAuth 实现）。
- **10 分钟版**：通读全文（能配置、调试、扩展 MCP 集成）。

### 2.1 一句话定义

OpenCode 的 MCP 集成采用"**AI SDK 原生集成 + 状态驱动的生命周期管理**"的设计：将 MCP 工具通过 `dynamicTool` 无缝转换为 Vercel AI SDK 的 `Tool` 类型，使用 `Instance.state()` 管理 MCP 客户端的连接状态，支持 connected/disabled/failed/needs_auth 等多种状态。

---

## 3. 核心组件详解

### 3.1 配置结构（Zod Schema）

**文件**: `packages/opencode/src/config/config.ts:525-586`

OpenCode 使用 Zod 进行类型安全的配置验证，支持 discriminated union 区分本地和远程 MCP：

```typescript
export const McpLocal = z.object({
  type: z.literal("local"),
  command: z.string().array(),        // 命令和参数
  environment: z.record(z.string(), z.string()).optional(),
  enabled: z.boolean().optional(),
  timeout: z.number().int().positive().optional(),
}).meta({ ref: "McpLocalConfig" })

export const McpOAuth = z.object({
  clientId: z.string().optional(),    // 可选，支持动态注册
  clientSecret: z.string().optional(),
  scope: z.string().optional(),
}).strict()

export const McpRemote = z.object({
  type: z.literal("remote"),
  url: z.string(),
  enabled: z.boolean().optional(),
  headers: z.record(z.string(), z.string()).optional(),
  oauth: z.union([McpOAuth, z.literal(false)]).optional(),
  timeout: z.number().int().positive().optional(),
}).meta({ ref: "McpRemoteConfig" })

// 使用 discriminated union 区分类型
export const Mcp = z.discriminatedUnion("type", [McpLocal, McpRemote])
export type Mcp = z.infer<typeof Mcp>
```

**配置示例** (opencode.json):

```json
{
  "mcp": {
    "filesystem": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "environment": { "NODE_ENV": "production" }
    },
    "github-api": {
      "type": "remote",
      "url": "https://api.github.com/mcp",
      "oauth": {
        "clientId": "my-client-id",
        "scope": "repo read:user"
      }
    }
  }
}
```

### 3.2 与 AI SDK 的集成：dynamicTool 转换

**文件**: `packages/opencode/src/mcp/index.ts:120-148`

这是 OpenCode MCP 集成的核心——将 MCP 工具转换为 AI SDK 的 `Tool`：

```typescript
import { dynamicTool, type Tool, jsonSchema, type JSONSchema7 } from "ai"
import { CallToolResultSchema } from "@modelcontextprotocol/sdk/types.js"

async function convertMcpTool(
  mcpTool: MCPToolDef,
  client: MCPClient,
  timeout?: number
): Promise<Tool> {
  const inputSchema = mcpTool.inputSchema

  // 构建 JSON Schema，确保 type 为 "object"
  const schema: JSONSchema7 = {
    ...(inputSchema as JSONSchema7),
    type: "object",
    properties: (inputSchema.properties ?? {}) as JSONSchema7["properties"],
    additionalProperties: false,
  }

  // 使用 AI SDK 的 dynamicTool 创建工具
  return dynamicTool({
    description: mcpTool.description ?? "",
    inputSchema: jsonSchema(schema),
    execute: async (args: unknown) => {
      // 实际调用 MCP 服务器的工具
      return client.callTool(
        {
          name: mcpTool.name,
          arguments: (args || {}) as Record<string, unknown>,
        },
        CallToolResultSchema,
        {
          resetTimeoutOnProgress: true,  // 有进度时重置超时
          timeout,
        },
      )
    },
  })
}
```

**关键点**：
- 使用 `dynamicTool` 而非静态工具定义，支持运行时动态发现
- `jsonSchema()` 将 MCP 的 schema 转换为 AI SDK 格式
- `resetTimeoutOnProgress` 确保长时间运行的工具不会超时

### 3.3 状态驱动的生命周期管理

**文件**: `packages/opencode/src/mcp/index.ts:163-210`

OpenCode 使用 `Instance.state()` 模式管理 MCP 客户端的生命周期：

```typescript
const state = Instance.state(
  // 初始化函数
  async () => {
    const cfg = await Config.get()
    const config = cfg.mcp ?? {}
    const clients: Record<string, MCPClient> = {}
    const status: Record<string, Status> = {}

    await Promise.all(
      Object.entries(config).map(async ([key, mcp]) => {
        if (!isMcpConfigured(mcp)) {
          log.error("Ignoring MCP config entry without type", { key })
          return
        }

        // 配置禁用则标记为 disabled
        if (mcp.enabled === false) {
          status[key] = { status: "disabled" }
          return
        }

        const result = await create(key, mcp).catch(() => undefined)
        if (result?.mcpClient) {
          clients[key] = result.mcpClient
        }
        status[key] = result?.status ?? { status: "failed", error: "unknown" }
      }),
    )

    return { status, clients }
  },
  // 清理函数
  async (state) => {
    await Promise.all(
      Object.values(state.clients).map((client) =>
        client.close().catch((error) => {
          log.error("Failed to close MCP client", { error })
        }),
      ),
    )
    pendingOAuthTransports.clear()
  },
)
```

**状态类型定义**:

```typescript
export const Status = z.discriminatedUnion("status", [
  z.object({ status: z.literal("connected") }),
  z.object({ status: z.literal("disabled") }),
  z.object({ status: z.literal("failed"), error: z.string() }),
  z.object({ status: z.literal("needs_auth") }),
  z.object({ status: z.literal("needs_client_registration"), error: z.string() }),
])
```

### 3.4 多传输支持与自动回退

**文件**: `packages/opencode/src/mcp/index.ts:304-360`

远程 MCP 服务器支持多种传输方式，自动回退：

```typescript
async function create(key: string, mcp: Config.Mcp) {
  if (mcp.type === "remote") {
    // OAuth 默认启用，除非显式设置为 false
    const oauthDisabled = mcp.oauth === false
    const oauthConfig = typeof mcp.oauth === "object" ? mcp.oauth : undefined

    let authProvider: McpOAuthProvider | undefined
    if (!oauthDisabled) {
      authProvider = new McpOAuthProvider(key, mcp.url, {
        clientId: oauthConfig?.clientId,
        clientSecret: oauthConfig?.clientSecret,
        scope: oauthConfig?.scope,
      })
    }

    // 定义传输尝试顺序
    const transports = [
      {
        name: "StreamableHTTP",
        transport: new StreamableHTTPClientTransport(new URL(mcp.url), {
          authProvider,
          requestInit: mcp.headers ? { headers: mcp.headers } : undefined,
        }),
      },
      {
        name: "SSE",
        transport: new SSEClientTransport(new URL(mcp.url), {
          authProvider,
          requestInit: mcp.headers ? { headers: mcp.headers } : undefined,
        }),
      },
    ]

    // 依次尝试直到成功
    for (const { name, transport } of transports) {
      try {
        const client = new Client({
          name: "opencode",
          version: Installation.VERSION,
        })
        await withTimeout(client.connect(transport), connectTimeout)
        registerNotificationHandlers(client, key)
        return { mcpClient: client, status: { status: "connected" } }
      } catch (error) {
        if (error instanceof UnauthorizedError) {
          pendingOAuthTransports.set(key, transport)
          return { status: { status: "needs_auth" } }
        }
        log.warn(`${name} failed, trying next...`, { error })
      }
    }
  }

  if (mcp.type === "local") {
    const transport = new StdioClientTransport({
      command: cmd,
      args,
      env: { ...process.env, ...mcp.environment },
    })
    const client = new Client({ name: "opencode", version: Installation.VERSION })
    await client.connect(transport)
    return { mcpClient: client, status: { status: "connected" } }
  }
}
```

### 3.5 OAuth 认证流程

**文件**: `packages/opencode/src/mcp/oauth-provider.ts`

```typescript
export class McpOAuthProvider implements McpAuthProvider {
  async getRequestHeaders(): Promise<Record<string, string>> {
    const token = await this.getAccessToken()
    return { Authorization: `Bearer ${token}` }
  }

  private async getAccessToken(): Promise<string> {
    // 1. 检查缓存的 token
    const cached = await this.tokenStorage.getToken()
    if (cached && !this.isExpired(cached)) {
      return cached.access_token
    }

    // 2. 使用 refresh token
    if (cached?.refresh_token) {
      return this.refreshAccessToken(cached.refresh_token)
    }

    // 3. 启动 OAuth 流程
    return this.startOAuthFlow()
  }

  private async startOAuthFlow(): Promise<string> {
    // 尝试动态客户端注册 (RFC 7591)
    if (!this.clientId) {
      const registration = await this.registerClient()
      this.clientId = registration.client_id
      this.clientSecret = registration.client_secret
    }

    // 构建授权 URL
    const authUrl = await this.buildAuthorizationUrl()

    // 打开浏览器
    await open(authUrl.toString())

    // 等待回调
    const code = await this.waitForCallback()

    // 交换 token
    return this.exchangeCodeForToken(code)
  }
}
```

---

## 4. Prompt 和 Resource 支持

除了工具调用，OpenCode 还支持 MCP 的 Prompt 和 Resource：

### 4.1 Prompt 获取与缓存

**文件**: `packages/opencode/src/mcp/index.ts:213-233`

```typescript
async function fetchPromptsForClient(clientName: string, client: Client) {
  const prompts = await client.listPrompts().catch((e) => {
    log.error("failed to get prompts", { clientName, error: e.message })
    return undefined
  })

  if (!prompts) return

  const commands: Record<string, PromptInfo & { client: string }> = {}

  for (const prompt of prompts.prompts) {
    // 清理名称中的非法字符
    const sanitizedClientName = clientName.replace(/[^a-zA-Z0-9_-]/g, "_")
    const sanitizedPromptName = prompt.name.replace(/[^a-zA-Z0-9_-]/g, "_")
    const key = sanitizedClientName + ":" + sanitizedPromptName

    commands[key] = { ...prompt, client: clientName }
  }

  return commands
}
```

### 4.2 Resource 获取

**文件**: `packages/opencode/src/mcp/index.ts:235-255`

```typescript
async function fetchResourcesForClient(clientName: string, client: Client) {
  const resources = await client.listResources().catch((e) => {
    log.error("failed to get resources", { clientName, error: e.message })
    return undefined
  })

  if (!resources) return

  const commands: Record<string, ResourceInfo & { client: string }> = {}

  for (const resource of resources.resources) {
    const sanitizedClientName = clientName.replace(/[^a-zA-Z0-9_-]/g, "_")
    const sanitizedResourceName = resource.name.replace(/[^a-zA-Z0-9_-]/g, "_")
    const key = sanitizedClientName + ":" + sanitizedResourceName

    commands[key] = { ...resource, client: clientName }
  }

  return commands
}
```

---

## 5. 与 Agent Loop 的集成

MCP 工具如何融入 OpenCode 的 Agent Loop：

1. **配置加载**: `Config.get()` 从多个层级加载配置（remote → global → project）
2. **MCP 初始化**: `Instance.state()` 创建并管理所有 MCP 客户端
3. **工具转换**: `convertMcpTool()` 将 MCP 工具转为 AI SDK `dynamicTool`
4. **Agent 执行**: `generateText()` / `generateObject()` 使用转换后的工具
5. **动态刷新**: 监听 `ToolListChangedNotification`，触发工具重新发现

---

## 6. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| MCP 客户端无法连接 | 检查 `type` 字段是否为 "local" 或 "remote" | `config/config.ts` |
| OAuth 认证失败 | 检查 `needs_auth` 状态，可能需要完成浏览器授权 | `mcp/oauth-provider.ts` |
| 工具不显示 | 查看 `status` 是否为 `connected` | `mcp/index.ts:187` |
| 传输层失败 | 检查 StreamableHTTP 和 SSE 回退日志 | `mcp/index.ts:347` |
| 配置不生效 | 检查配置加载顺序，可能被高层级覆盖 | `config/config.ts:71-78` |

---

## 7. 架构特点总结

- **AI SDK 原生集成**: 通过 `dynamicTool` 无缝融入 Vercel AI SDK
- **状态驱动**: 使用 `Instance.state()` 管理 MCP 客户端生命周期
- **多层级配置**: 支持 remote → global → project → inline 五级配置
- **自动传输回退**: StreamableHTTP → SSE 自动尝试
- **动态 OAuth**: 支持 RFC 7591 动态客户端注册
- **完整类型安全**: 使用 Zod 进行配置验证和类型推断
