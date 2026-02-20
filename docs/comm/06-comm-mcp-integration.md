# MCP 集成对比

## 1. 概念定义

**MCP（Model Context Protocol）** 是 Anthropic 推出的开放协议，用于标准化 AI 模型与外部工具、数据源之间的集成。它允许 Agent 动态发现和调用外部服务提供的工具。

### 核心概念

- **MCP Server**：提供工具服务的外部进程
- **MCP Client**：与 Server 通信的客户端
- **Tool**：Server 暴露的具体功能
- **Transport**：通信方式（stdio、HTTP、SSE）

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 目前**不支持 MCP 协议**。它使用自有的 Bundle 系统进行工具管理。

| 特性 | 支持情况 |
|------|----------|
| MCP Client | 否 |
| MCP Server | 否 |
| 替代方案 | Bundle 配置 |

### 2.2 Codex

**实现概述**

Codex 通过 `McpClientManager` 实现 MCP 集成，支持从配置启动 MCP Server 并动态发现工具。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Session (会话层)                                         │
│  ├── mcp_client_manager: Arc<McpClientManager>          │
│  └── parse_mcp_tool_name()      MCP 工具名解析          │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  McpClientManager (管理层)                                │
│  ├── start_configured_mcp_servers()  启动配置服务       │
│  ├── start_extension()               加载扩展服务       │
│  ├── maybe_discover_mcp_server()     发现并注册工具     │
│  └── stop()                          清理连接           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  MCP Servers (外部服务)                                   │
│  ├── Server 1 (stdio)               本地进程            │
│  ├── Server 2 (http)                HTTP 服务           │
│  └── Server 3 (sse)                 SSE 服务            │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| McpClientManager | `codex-rs/core/src/mcp/` | - | MCP 管理 |
| ToolRouter MCP | `codex-rs/core/src/tools/router.rs` | 100 | 工具解析 |
| Session MCP | `codex-rs/core/src/session.rs` | 100 | 会话集成 |

**MCP 工具调用流程**

```rust
// 1. 解析 MCP 工具名
if let Some((server, tool)) = session.parse_mcp_tool_name(&name).await {
    return Ok(Some(ToolCall {
        tool_name: name,
        call_id,
        payload: ToolPayload::Mcp { server, tool, raw_arguments: arguments },
    }));
}

// 2. Handler 执行
async fn handle(&self,
    invocation: ToolInvocation
) -> Result<ToolOutput, FunctionCallError> {
    if let ToolPayload::Mcp { server, tool, raw_arguments } = &invocation.payload {
        // 调用 MCP Server
        let result = self.mcp_client.call_tool(
            server,
            tool,
            raw_arguments
        ).await?;

        return Ok(ToolOutput::Mcp { result: Ok(result) });
    }
    // ...
}
```

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 通过 `McpClientManager` 和 `DiscoveredMCPTool` 实现 MCP 集成，支持三层工具来源（Built-in、Discovered、MCP）。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  ToolRegistry (工具注册层)                                │
│  ├── allKnownTools: Map            工具映射             │
│  ├── discoverAllTools()            发现工具             │
│  └── sortTools()                   排序 (MCP 优先级 2)  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  McpClientManager (MCP 管理层)                            │
│  ├── startConfiguredMcpServers()   启动配置服务         │
│  ├── startExtension()              加载扩展             │
│  ├── maybeDiscoverMcpServer()      发现并注册           │
│  └── clients: Map<string, McpClient>  客户端映射         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  DiscoveredMCPTool (MCP 工具包装)                         │
│  └── 包装 MCP 工具为 DeclarativeTool                    │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| McpClientManager | `packages/core/src/mcp/` | - | MCP 管理器 |
| DiscoveredMCPTool | `packages/core/src/tools/` | - | MCP 工具类 |
| ToolRegistry | `packages/core/src/tools/registry.ts` | 50 | 工具注册 |

**发现状态机**

```typescript
enum MCPDiscoveryState {
    NOT_STARTED = 'not_started',
    IN_PROGRESS = 'in_progress',
    COMPLETED = 'completed',
}
```

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 通过 **ACP（Agent Communication Protocol）** 协议实现多 Agent 协作，支持 MCP Server 作为工具来源。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  KimiSoul (Agent 核心)                                    │
│  └── toolset: KimiToolset           工具集合            │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ACP Client (ACP 客户端)                                  │
│  └── acp_mcp_servers_to_mcp_config() 配置转换           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  MCP Servers (多种传输)                                   │
│  ├── HTTP Transport                                     │
│  ├── SSE Transport                                      │
│  └── Stdio Transport                                    │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| ACP MCP | `kimi-cli/src/kimi_cli/acp/mcp.py` | - | MCP 配置转换 |
| Kosong | `kimi-cli/src/kimi_cli/agent/kosong.py` | 50 | 工具初始化 |
| MCPServer | `kimi-cli/src/kimi_cli/acp/schema.py` | - | Server 定义 |

**配置转换**

```python
def acp_mcp_servers_to_mcp_config(mcp_servers: list[MCPServer]) -> MCPConfig:
    """将 ACP MCP Server 配置转换为内部 MCPConfig。"""
    for server in mcp_servers:
        match server:
            case acp.schema.HttpMcpServer():
                return {"url": server.url, "transport": "http", ...}
            case acp.schema.SseMcpServer():
                return {"url": server.url, "transport": "sse", ...}
            case acp.schema.McpServerStdio():
                return {"command": server.command, "transport": "stdio", ...}
```

### 2.5 OpenCode

**实现概述**

OpenCode 通过 `ToolRegistry` 的自定义工具加载机制支持 MCP Server，将 MCP 工具作为外部工具源。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  ToolRegistry (工具注册层)                                │
│  ├── state()                       初始化               │
│  │   ├── 加载自定义工具                                    │
│  │   └── 加载插件工具                                      │
│  └── register(tool)                动态注册             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  MCP Server (可选)                                        │
│  └── mcp-server.ts                 MCP 服务器实现       │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  External Tools (外部工具)                                │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| ToolRegistry | `packages/opencode/src/tool/registry.ts` | 1 | 工具注册 |
| MCP Server | `packages/opencode/src/mcp/mcp-server.ts` | - | MCP 服务 |

---

## 3. 相同点总结

### 3.1 支持的传输方式

| 传输方式 | Codex | Gemini CLI | Kimi CLI | OpenCode |
|----------|-------|------------|----------|----------|
| stdio | 是 | 是 | 是 | 是 |
| HTTP | 是 | 是 | 是 | 是 |
| SSE | - | 是 | 是 | 是 |

### 3.2 通用功能

支持 MCP 的 Agent 都具备：

- **配置管理**：从配置文件读取 MCP Server 配置
- **生命周期管理**：启动、连接、断开、清理
- **工具发现**：动态获取 Server 提供的工具列表
- **工具调用**：将 MCP 工具调用转发给 Server

### 3.3 工具集成方式

```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Agent     │────▶│ MCP Client  │────▶│ MCP Server  │
│  Tool Call  │     │             │     │             │
└──────┬──────┘     └─────────────┘     └──────┬──────┘
       │                                        │
       │◄───────────────────────────────────────│
       │           工具执行结果                 │
       ▼
┌─────────────┐
│  结果回注   │
│  给 LLM     │
└─────────────┘
```

---

## 4. 不同点对比

### 4.1 MCP 支持情况

| Agent | 支持状态 | 实现方式 | 备注 |
|-------|----------|----------|------|
| SWE-agent | 否 | - | 使用 Bundle 替代 |
| Codex | 是 | McpClientManager | Rust 实现 |
| Gemini CLI | 是 | McpClientManager | TypeScript |
| Kimi CLI | 是 | ACP 协议 | HTTP/SSE/Stdio |
| OpenCode | 是 | ToolRegistry 扩展 | 插件方式 |

### 4.2 工具优先级

| Agent | MCP 工具优先级 | 冲突处理 |
|-------|----------------|----------|
| SWE-agent | - | - |
| Codex | 与内置工具平等 | 后注册优先 |
| Gemini CLI | 2（最低） | Built-in < Discovered < MCP |
| Kimi CLI | 与内置工具平等 | 配置决定 |
| OpenCode | 与自定义工具平等 | 配置决定 |

### 4.3 发现机制

| Agent | 发现时机 | 状态管理 | 热重载 |
|-------|----------|----------|--------|
| Codex | 启动时 | 无显式状态 | 否 |
| Gemini CLI | 启动/扩展加载 | MCPDiscoveryState | 部分支持 |
| Kimi CLI | 启动时 | 无 | 否 |
| OpenCode | 初始化时 | 无 | 是 |

### 4.4 配置方式

| Agent | 配置位置 | 格式 | 动态配置 |
|-------|----------|------|----------|
| Codex | ~/.codex/config.toml | TOML | 否 |
| Gemini CLI | ~/.gemini/config.json | JSON | 否 |
| Kimi CLI | ~/.kimi/config.yaml | YAML | 否 |
| OpenCode | ~/.opencode/config.json | JSON | 是 |

---

## 5. 源码索引

### 5.1 MCP 管理器

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/core/src/mcp/` | - | McpClientManager |
| Gemini CLI | `packages/core/src/mcp/` | - | McpClientManager |
| Kimi CLI | `kimi-cli/src/kimi_cli/acp/mcp.py` | 1 | 配置转换 |
| OpenCode | `packages/opencode/src/mcp/` | - | MCP Server |

### 5.2 MCP 工具调用

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/core/src/tools/router.rs` | 100 | ToolPayload::Mcp |
| Gemini CLI | `packages/core/src/tools/` | - | DiscoveredMCPTool |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/kosong.py` | 100 | MCP 工具初始化 |
| OpenCode | `packages/opencode/src/tool/` | - | 外部工具加载 |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 纯 MCP 工具链 | Codex/Gemini CLI | 原生支持完善 |
| 混合工具需求 | Gemini CLI | 三层工具来源 |
| ACP 生态 | Kimi CLI | ACP 协议支持 |
| 插件扩展 | OpenCode | 动态加载能力强 |
| 学术研究 | SWE-agent | 虽然无 MCP，但 Bundle 灵活 |
