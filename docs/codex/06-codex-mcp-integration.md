# MCP 集成（codex）

本文基于 `./codex/codex-rs` 源码，解释 codex 如何实现 MCP (Model Context Protocol) 接入，将外部 MCP 服务器的能力集成到 Agent Loop 中。

---

## 1. 先看全局（流程图）

### 1.1 MCP 工具调用完整流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  配置阶段：MCP Server 配置加载                                    │
│  ┌────────────────────────────────────────┐                     │
│  │ codex.yaml / 默认配置                   │                     │
│  │  └── mcp_servers:                      │                     │
│  │      ├── server1: {stdio}              │                     │
│  │      └── server2: {streamable_http}    │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  初始化阶段：连接管理与工具发现                                   │
│  ┌────────────────────────────────────────┐                     │
│  │ McpConnectionManager::new()            │                     │
│  │  ├── 为每个 server 创建 RmcpClient     │                     │
│  │  ├── initialize() MCP 握手             │                     │
│  │  ├── list_tools() 获取工具列表         │                     │
│  │  └── qualify_tools() 命名空间处理      │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  注册阶段：工具注册到 ToolRegistry                               │
│  ┌────────────────────────────────────────┐                     │
│  │ ToolRouter::build_tools()              │                     │
│  │  └── 所有 MCP 工具注册为 McpHandler    │                     │
│  │      工具名格式: mcp__{server}__{tool}  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  调用阶段：Agent Loop 中的工具执行                               │
│  ┌────────────────────────────────────────┐                     │
│  │ run_turn() → 模型输出 tool call        │                     │
│  │  └── OutputItemDone → build_tool_call  │                     │
│  │       └── ToolPayload::Mcp             │                     │
│  │            └── McpHandler::handle()    │                     │
│  │                 └── handle_mcp_tool_call│                    │
│  │                      ├── maybe_request_│                     │
│  │                      │   mcp_tool_approval (审批)             │
│  │                      ├── emit McpToolCallBegin                 │
│  │                      ├── session.call_tool()                   │
│  │                      └── emit McpToolCallEnd                   │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件架构图

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Loop 层                            │
│  ┌─────────────────┐  ┌─────────────────────────────────────┐  │
│  │   run_turn()    │  │     ToolRouter / ToolRegistry       │  │
│  │                 │  │  ┌───────────────────────────────┐  │  │
│  │  ┌───────────┐  │  │  │     McpHandler                │  │  │
│  │  │  Sampling │──┼──┼──┤  (处理所有 MCP 工具调用)       │  │  │
│  │  └───────────┘  │  │  └───────────────┬───────────────┘  │  │
│  └─────────────────┘  └──────────────────┼────────────────────┘  │
└──────────────────────────────────────────┼───────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MCP 连接管理层                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              McpConnectionManager                        │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │ RmcpClient  │  │ RmcpClient  │  │   ...           │  │   │
│  │  │  (server1)  │  │  (server2)  │  │                 │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────────┘  │   │
│  └─────────┼────────────────┼───────────────────────────────┘   │
└────────────┼────────────────┼────────────────────────────────────┘
             │                │
             ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      传输层 (rmcp SDK)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   Stdio     │  │    SSE      │  │   StreamableHTTP        │ │
│  │  Transport  │  │  Transport  │  │    Transport            │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
             │                │                │
             ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      外部 MCP Servers                           │
│         ┌──────────┐  ┌──────────┐  ┌─────────────────┐         │
│         │ filesystem│  │  github  │  │   postgres      │         │
│         │  server   │  │  server  │  │   server        │         │
│         └──────────┘  └──────────┘  └─────────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `3.1`（知道工具名格式 `mcp__server__tool` 和调用流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道架构分层、命名空间、传输支持）。
- **10 分钟版**：通读全文（能定位配置、调试工具调用问题）。

### 2.1 一句话定义

codex 的 MCP 集成采用"**中心化连接管理 + 统一工具命名空间**"的设计：所有 MCP 服务器由 `McpConnectionManager` 统一管理，工具名按 `mcp__{server}__{tool}` 格式命名以避免冲突，通过 `McpHandler` 统一处理所有 MCP 工具调用。

---

## 3. 核心组件详解

### 3.1 配置结构

**文件**: `core/src/config/types.rs:61-142`

```rust
#[derive(Serialize, Debug, Clone, PartialEq)]
pub struct McpServerConfig {
    #[serde(flatten)]
    pub transport: McpServerTransportConfig,
    pub enabled: bool,
    pub required: bool,
    pub disabled_reason: Option<McpServerDisabledReason>,
    pub startup_timeout_sec: Option<Duration>,
    pub tool_timeout_sec: Option<Duration>,
    pub enabled_tools: Option<Vec<String>>,  // 允许列表
    pub disabled_tools: Option<Vec<String>>, // 禁止列表
    pub scopes: Option<Vec<String>>,         // OAuth scopes
}

pub enum McpServerTransportConfig {
    Stdio {
        command: String,
        args: Vec<String>,
        env: Option<HashMap<String, String>>,
        cwd: Option<PathBuf>,
    },
    StreamableHttp {
        url: String,
        bearer_token_env_var: Option<String>,
        http_headers: Option<HashMap<String, String>>,
    },
}
```

**配置示例** (codex.yaml):

```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    enabled: true
  github:
    url: https://api.github.com/mcp
    transport: streamable_http
    bearer_token_env_var: GITHUB_TOKEN
```

### 3.2 工具命名空间机制

**文件**: `core/src/mcp_connection_manager.rs:77-163`

为避免不同 MCP 服务器的工具名冲突，codex 使用**完全限定工具名**格式：

```rust
const MCP_TOOL_NAME_DELIMITER: &str = "__";
const MAX_TOOL_NAME_LENGTH: usize = 64;

fn qualify_tools<I>(tools: I) -> HashMap<String, ToolInfo>
where I: IntoIterator<Item = ToolInfo> {
    for tool in tools {
        // 原始格式: mcp__{server_name}__{tool_name}
        let qualified_name_raw = format!(
            "mcp{}{}{}{}",
            MCP_TOOL_NAME_DELIMITER,
            tool.server_name,
            MCP_TOOL_NAME_DELIMITER,
            tool.tool_name
        );
        // 清理非法字符（OpenAI API 限制：^[a-zA-Z0-9_-]+$）
        let mut qualified_name = sanitize_responses_api_tool_name(&qualified_name_raw);
        // 长度限制处理（超过64字符使用 SHA1 哈希）
        if qualified_name.len() > MAX_TOOL_NAME_LENGTH {
            let sha1_str = sha1_hex(&qualified_name_raw);
            let prefix_len = MAX_TOOL_NAME_LENGTH - sha1_str.len();
            qualified_name = format!("{}{}", &qualified_name[..prefix_len], sha1_str);
        }
        qualified_tools.insert(qualified_name, tool);
    }
}
```

**命名转换示例**:

| 服务器 | 原始工具名 | 完全限定名 | 说明 |
|--------|-----------|-----------|------|
| filesystem | read_file | `mcp__filesystem__read_file` | 标准格式 |
| github | create_issue | `mcp__github__create_issue` | 标准格式 |
| my.server | tool.name | `mcp__my_server__tool_name` | 非法字符替换为 `_` |

### 3.3 连接管理器 (McpConnectionManager)

**文件**: `core/src/mcp_connection_manager.rs`

`McpConnectionManager` 是 MCP 集成的核心，负责：

1. **多服务器管理**: 维护 `HashMap<String, AsyncManagedClient>`
2. **工具聚合**: `list_all_tools()` 聚合所有服务器的工具
3. **生命周期管理**: 初始化、关闭、重连
4. **沙箱状态同步**: 向 MCP 服务器传播 sandbox 配置

```rust
pub(crate) struct McpConnectionManager {
    clients: HashMap<String, AsyncManagedClient>,
    elicitation_requests: ElicitationRequestManager,
}

impl McpConnectionManager {
    pub async fn list_all_tools(&self) -> HashMap<String, ToolInfo> {
        // 聚合所有服务器的工具
        for (server_name, managed_client) in &self.clients {
            let tools = client.list_tools_with_connector_ids(None, timeout).await?;
            tools.extend(qualify_tools(filter_tools(server_tools, tool_filter)));
        }
    }
}
```

### 3.4 工具处理器 (McpHandler)

**文件**: `core/src/tools/handlers/mcp.rs`

所有 MCP 工具调用统一由 `McpHandler` 处理：

```rust
pub struct McpHandler;

#[async_trait]
impl ToolHandler for McpHandler {
    fn kind(&self) -> ToolKind {
        ToolKind::Mcp
    }

    async fn handle(&self, invocation: ToolInvocation) -> Result<ToolOutput, FunctionCallError> {
        let payload = match payload {
            ToolPayload::Mcp { server, tool, raw_arguments } => (server, tool, raw_arguments),
            _ => return Err(...),
        };

        let response = handle_mcp_tool_call(
            Arc::clone(&session),
            turn.as_ref(),
            call_id.clone(),
            server,
            tool,
            arguments_str,
        ).await;

        match response {
            ResponseInputItem::McpToolCallOutput { result, .. } =>
                Ok(ToolOutput::Mcp { result }),
            ...
        }
    }
}
```

### 3.5 工具调用流程 (handle_mcp_tool_call)

**文件**: `core/src/mcp_tool_call.rs:33-150`

```rust
pub(crate) async fn handle_mcp_tool_call(
    sess: Arc<Session>,
    turn_context: &TurnContext,
    call_id: String,
    server: String,
    tool_name: String,
    arguments: String,
) -> ResponseInputItem {
    // 1. 解析参数
    let arguments_value = serde_json::from_str::<serde_json::Value>(&arguments)?;

    // 2. 检查审批（destructive/open_world 工具）
    if let Some(decision) = maybe_request_mcp_tool_approval(...).await {
        match decision {
            McpToolApprovalDecision::Accept => {
                // 3. 发送开始事件
                emit McpToolCallBegin;

                // 4. 执行工具调用
                let result = sess.call_tool(&server, &tool_name, arguments_value).await;

                // 5. 发送结束事件
                emit McpToolCallEnd;

                ResponseInputItem::McpToolCallOutput { call_id, result }
            }
            McpToolApprovalDecision::Decline => { /* 处理拒绝 */ }
        }
    }
}
```

---

## 4. 传输层支持

codex 通过底层的 `rmcp` SDK 支持多种 MCP 传输：

| 传输类型 | 使用场景 | 配置方式 |
|---------|---------|---------|
| **Stdio** | 本地 MCP 服务器（Node.js、Python 脚本） | `command` + `args` |
| **StreamableHTTP** | 远程 MCP 服务器（需要 HTTP 支持） | `url` + `bearer_token_env_var` |

**Stdio 传输示例**:
```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env:
      NODE_ENV: production
```

**StreamableHTTP 传输示例**:
```yaml
mcp_servers:
  remote_api:
    url: https://api.example.com/mcp
    bearer_token_env_var: API_TOKEN
    http_headers:
      X-Custom-Header: value
```

---

## 5. Codex Apps MCP 服务器

codex 内置了对 OpenAI Apps/Connectors 的特殊支持：

**文件**: `core/src/mcp/mod.rs:118-143`

```rust
fn codex_apps_mcp_server_config(config: &Config, auth: Option<&CodexAuth>) -> McpServerConfig {
    McpServerConfig {
        transport: McpServerTransportConfig::StreamableHttp {
            url: codex_apps_mcp_url(config),  // https://api.openai.com/v1/connectors/mcp/
            bearer_token_env_var: codex_apps_mcp_bearer_token_env_var(),
            http_headers: codex_apps_mcp_http_headers(auth),
            env_http_headers: None,
        },
        enabled: true,
        required: false,
        startup_timeout_sec: Some(Duration::from_secs(30)),
        ...
    }
}
```

当 `features.apps` 启用时，codex 自动添加内置的 Codex Apps MCP 服务器，允许访问 OpenAI 的 Connectors 生态。

---

## 6. 与 Agent Loop 的集成

MCP 工具如何融入 codex 的 Agent Loop：

1. **Session 初始化**: `Session::new()` 创建 `McpConnectionManager`
2. **工具发现**: `list_all_tools()` 获取所有 MCP 工具并注册
3. **Prompt 构建**: `built_tools()` 将 MCP 工具加入模型上下文
4. **工具调用**: 模型输出 → `ToolRouter` 解析 → `McpHandler` 执行 → `call_tool()` 调用远程 MCP 服务器
5. **结果回注**: `McpToolCallOutput` 写入历史，触发下一轮采样

---

## 7. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| MCP 服务器无法启动 | 检查 `startup_timeout_sec` 和命令配置 | `mcp_connection_manager.rs` |
| 工具名冲突 | 查看 `qualify_tools()` 的命名转换 | `mcp_connection_manager.rs:123-163` |
| 工具调用超时 | 检查 `tool_timeout_sec` 设置 | `config/types.rs` |
| 审批流程问题 | 查看 `maybe_request_mcp_tool_approval()` | `mcp_tool_call.rs` |
| OAuth 认证失败 | 检查 `bearer_token_env_var` 和 headers | `mcp/mod.rs` |

---

## 8. 架构特点总结

- **命名空间隔离**: `mcp__{server}__{tool}` 格式避免工具名冲突
- **统一处理**: 所有 MCP 工具通过单个 `McpHandler` 处理，简化代码
- **中心化连接管理**: `McpConnectionManager` 统一管理多个服务器连接
- **灵活传输**: 支持 stdio 和 streamable_http 两种传输方式
- **内置集成**: 原生支持 OpenAI Codex Apps/Connectors
- **安全控制**: 支持工具审批、沙箱状态同步、允许/禁止列表
