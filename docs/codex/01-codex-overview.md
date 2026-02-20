# Codex 概述文档

## 1. 项目简介

**Codex** 是 OpenAI 推出的官方 CLI Agent，采用 Rust 语言实现，旨在提供高效、安全的 AI 辅助编程体验。

### 项目定位和目标
- 定位为开发者日常编程助手，支持交互式 TUI 和非交互式执行模式
- 强调安全性，提供多层级沙箱机制（Seatbelt/Landlock/Windows 受限令牌）
- 支持复杂的 Agent 协作模式（Collaboration Mode）
- 提供 MCP（Model Context Protocol）服务器扩展能力

### 技术栈
- **语言**: Rust
- **核心依赖**:
  - `tokio` - 异步运行时
  - `clap` - CLI 参数解析
  - `reqwest`/`tokio-tungstenite` - HTTP/WebSocket 通信
  - `serde`/`serde_json` - 序列化
  - `rmcp` - MCP 协议支持

### 官方仓库
- https://github.com/openai/codex

---

## 2. 架构概览

### 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Layer                            │
│  (codex/codex-rs/cli/src/main.rs:545)                      │
│  - MultitoolCli: 命令解析                                   │
│  - Subcommand: exec/review/login/mcp/etc                   │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      TUI Layer                              │
│  (codex/codex-rs/tui/)                                      │
│  - 交互式界面渲染                                           │
│  - 用户输入处理                                             │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Core Agent Layer                         │
│  (codex/codex-rs/core/src/)                                 │
│  ├─ codex.rs:266 - Codex 主结构体                           │
│  ├─ codex.rs:292 - spawn() 初始化                           │
│  └─ agent/: Agent 控制与状态管理                            │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Session Layer                            │
│  (codex/codex-rs/core/src/codex.rs:806)                     │
│  ├─ Session: 会话管理                                       │
│  ├─ SessionState: 状态存储 (state/session.rs:16)           │
│  └─ TurnContext: 单次回合上下文                             │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Tools Layer                              │
│  (codex/codex-rs/core/src/tools/)                           │
│  ├─ registry.rs:57 - ToolRegistry                           │
│  ├─ spec.rs - 工具定义                                      │
│  └─ handlers/ - 工具实现                                    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Model Client Layer                        │
│  (codex/codex-rs/core/src/client.rs:150)                    │
│  ├─ ModelClient: 会话级客户端                               │
│  ├─ ModelClientSession: 回合级会话                          │
│  └─ Responses API / WebSocket 通信                          │
└─────────────────────────────────────────────────────────────┘
```

### 各层职责说明

| 层级 | 文件路径 | 核心职责 |
|------|----------|----------|
| CLI | `cli/src/main.rs` | 命令解析、子命令分发、配置覆盖 |
| TUI | `tui/src/` | 交互式界面、事件循环、渲染 |
| Core | `core/src/codex.rs` | Agent 生命周期、消息路由、事件分发 |
| Session | `core/src/codex.rs` | 会话状态管理、历史记录、Token 追踪 |
| Tools | `core/src/tools/` | 工具注册、调度、执行、沙箱控制 |
| Client | `core/src/client.rs` | 模型 API 调用、流式响应、重试逻辑 |

### 核心组件列表

1. **Codex** (codex.rs:266) - 主入口，提供 spawn 方法创建会话
2. **Session** (codex.rs:806) - 管理会话生命周期和状态
3. **TurnContext** (codex.rs:919) - 单次交互的完整上下文
4. **ToolRegistry** (tools/registry.rs:57) - 工具注册与调度
5. **ModelClient** (client.rs:150) - 模型 API 客户端
6. **AgentControl** - Agent 执行控制（暂停/恢复/停止）

---

## 3. 入口与 CLI

### 入口文件路径
```
codex/codex-rs/cli/src/main.rs:545
```

### CLI 参数解析方式

使用 `clap` 库进行命令解析，支持以下主要命令：

```rust
// main.rs:67-146
#[derive(Debug, Parser)]
struct MultitoolCli {
    #[clap(flatten)]
    pub config_overrides: CliConfigOverrides,
    #[clap(flatten)]
    pub feature_toggles: FeatureToggles,
    #[clap(flatten)]
    interactive: TuiCli,
    #[clap(subcommand)]
    subcommand: Option<Subcommand>,
}

enum Subcommand {
    Exec(ExecCli),           // 非交互式执行
    Review(ReviewArgs),      // 代码审查
    Login(LoginCommand),     // 登录管理
    Mcp(McpCli),             // MCP 服务器管理
    Resume(ResumeCommand),   // 恢复会话
    Fork(ForkCommand),       // 分叉会话
    // ... 更多命令
}
```

### 启动流程

```
main()@main.rs:545
  ├─ cli_main()@main.rs:555
  │   ├─ MultitoolCli::parse() 解析参数
  │   ├─ match subcommand
  │   │   ├─ None -> run_interactive_tui() 启动交互模式
  │   │   ├─ Some(Subcommand::Exec) -> codex_exec::run_main()
  │   │   ├─ Some(Subcommand::Resume) -> 恢复会话
  │   │   └─ ... 其他子命令
  │   └─ handle_app_exit() 处理退出
```

---

## 4. Agent 循环机制

### 主循环代码位置

```
codex/codex-rs/core/src/codex.rs:264-272 (Codex 结构体定义)
codex/codex-rs/core/src/codex.rs:292-300 (spawn 方法)
codex/codex-rs/core/src/agent/mod.rs (Agent 控制逻辑)
```

### 流程图（文本形式）

```
┌─────────────┐
│   启动      │
│ spawn()     │
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│  创建 Session │───▶│ 初始化状态   │
│  new()      │     │ RolloutRecorder
└──────┬──────┘     └──────┬──────┘
       │                    │
       ▼                    ▼
┌─────────────────────────────────────┐
│            事件循环                  │
│  while let Ok(event) = rx.recv()    │
│  ┌───────────────────────────────┐  │
│  │ 1. 接收 Submission (用户输入) │  │
│  │ 2. 创建 TurnContext           │  │
│  │ 3. 调用 model_client.stream() │  │
│  │ 4. 处理流式响应                │  │
│  │ 5. 执行 Tool Calls            │  │
│  │ 6. 发送事件到 TUI              │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
       │
       ▼
┌─────────────┐
│  会话结束   │
│ 保存状态    │
└─────────────┘
```

### 单次循环的执行步骤

1. **接收 Submission** - 从 `tx_sub` 通道接收用户输入或系统指令
2. **创建 TurnContext** (codex.rs:919) - 构建单次交互的完整上下文
3. **构建 Prompt** - 组合系统提示、历史记录、当前输入
4. **调用模型** - 通过 `ModelClientSession::stream()` 发起流式请求
5. **处理响应流** - 解析 SSE/WebSocket 事件，处理内容增量
6. **执行工具调用** - 通过 `ToolRegistry::dispatch()` 执行工具
7. **发送事件** - 通过 `tx_event` 通道发送事件到 TUI
8. **更新状态** - 记录历史、Token 使用、Rate Limit

### 循环终止条件

- 用户主动退出（ExitReason::UserRequested）
- 发生致命错误（ExitReason::Fatal）
- 会话被明确关闭
- 达到最大回合数限制

---

## 5. 工具系统

### 工具定义方式

工具定义位于 `codex/codex-rs/core/src/tools/spec.rs`，使用 JSON Schema 格式：

```rust
// tools/spec.rs
pub struct ToolsConfig {
    pub specs: Vec<ConfiguredToolSpec>,
    pub web_search_mode: Option<WebSearchMode>,
}

pub struct ConfiguredToolSpec {
    pub spec: ToolSpec,
    pub supports_parallel_tool_calls: bool,
}
```

### 工具注册表位置

```
codex/codex-rs/core/src/tools/registry.rs:57
```

```rust
pub struct ToolRegistry {
    handlers: HashMap<String, Arc<dyn ToolHandler>>,
}

pub trait ToolHandler: Send + Sync {
    fn kind(&self) -> ToolKind;
    async fn is_mutating(&self, invocation: &ToolInvocation) -> bool;
    async fn handle(&self, invocation: ToolInvocation) -> Result<ToolOutput, FunctionCallError>;
}
```

### 工具执行流程

```
接收 Tool Call
      │
      ▼
┌─────────────────┐
│ ToolRegistry    │
│ ::dispatch()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐    ┌─────────────────┐
│ 查找 Handler    │───▶│ 未找到 -> 错误  │
└────────┬────────┘    └─────────────────┘
         │
         ▼
┌─────────────────┐    ┌─────────────────┐
│ is_mutating()?  │───▶│ 是 -> 等待审批   │
└────────┬────────┘    │ (tool_call_gate) │
         │             └─────────────────┘
         ▼
┌─────────────────┐
│ handler.handle()│
│ 执行工具        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 返回 ToolOutput │
│ 转换为响应项    │
└─────────────────┘
```

### 审批机制

```rust
// tools/registry.rs:137-156
let is_mutating = handler.is_mutating(&invocation).await;
if is_mutating {
    tracing::trace!("waiting for tool gate");
    invocation_for_tool.turn.tool_call_gate.wait_ready().await;
    tracing::trace!("tool gate released");
}
```

- 变更性操作（mutating）需要等待审批
- `tool_call_gate` 是 `ReadinessFlag` 类型，用于控制执行时机
- 审批策略由 `approval_policy` 配置控制

---

## 6. 状态管理

### Session 状态存储位置

```
codex/codex-rs/core/src/state/session.rs:16
```

```rust
pub(crate) struct SessionState {
    pub(crate) session_configuration: SessionConfiguration,
    pub(crate) history: ContextManager,        // 历史记录管理
    pub(crate) latest_rate_limits: Option<RateLimitSnapshot>,
    pub(crate) server_reasoning_included: bool,
    pub(crate) dependency_env: HashMap<String, String>,
    pub(crate) mcp_dependency_prompted: HashSet<String>,
    pub(crate) initial_context_seeded: bool,
    pub(crate) previous_model: Option<String>,
    pub(crate) startup_regular_task: Option<RegularTask>,
    pub(crate) active_mcp_tool_selection: Option<Vec<String>>,
    pub(crate) active_connector_selection: HashSet<String>,
}
```

### Checkpoint 机制

- **RolloutRecorder** (codex.rs:1073-1080) - 持久化会话事件
- **状态数据库** (`state_db`) - SQLite 存储（可选）
- **事件持久化模式**: Limited / Extended

```rust
pub struct RolloutRecorderParams {
    pub conversation_id: ThreadId,
    pub forked_from_id: Option<ThreadId>,
    pub session_source: SessionSource,
    pub base_instructions: BaseInstructions,
    pub event_persistence_mode: EventPersistenceMode,
}
```

### 历史记录管理

**ContextManager** 负责管理对话历史：

```rust
// context_manager.rs
pub struct ContextManager {
    items: Vec<ResponseItem>,
    token_info: Option<TokenUsageInfo>,
}
```

- 支持历史记录截断（Truncation）
- 支持 Compact 压缩（当 Token 超限）
- 支持从检查点恢复

### 状态恢复方式

```
Resume 流程:
1. 从命令行获取 session_id 或使用选择器
2. 加载 rollout 文件
3. 恢复 SessionState
4. 重建 ContextManager 历史
5. 继续会话

Fork 流程:
1. 复制原会话历史
2. 生成新的 conversation_id
3. 保留原状态但独立演化
```

---

## 7. 模型调用方式

### 支持的模型提供商

- **OpenAI** - 官方 API（默认）
- **Azure OpenAI** - 企业部署
- **OpenRouter** - 第三方聚合
- **OSS 模型** - 本地/自托管模型

### 模型调用封装位置

```
codex/codex-rs/core/src/client.rs:150
```

```rust
pub struct ModelClient {
    state: Arc<ModelClientState>,
}

pub struct ModelClientSession {
    client: ModelClient,
    connection: Option<ApiWebSocketConnection>,
    websocket_last_request: Option<ResponsesApiRequest>,
    turn_state: Arc<OnceLock<String>>,  // Sticky routing
}
```

### 流式响应处理

```rust
// client.rs:887-932
pub async fn stream(...) -> Result<ResponseStream> {
    match wire_api {
        WireApi::Responses => {
            let websocket_enabled = self.client.responses_websocket_enabled(model_info);
            if websocket_enabled {
                match self.stream_responses_websocket(...).await? {
                    WebsocketStreamOutcome::Stream(stream) => return Ok(stream),
                    WebsocketStreamOutcome::FallbackToHttp => {
                        self.try_switch_fallback_transport(...);
                    }
                }
            }
            self.stream_responses_api(...).await
        }
    }
}
```

### Token 管理

```rust
// state/session.rs:83-95
pub(crate) fn update_token_info_from_usage(...) {
    self.history.update_token_info(usage, model_context_window);
}

pub(crate) fn token_info(&self) -> Option<TokenUsageInfo> {
    self.history.token_info()
}
```

- Token 使用从 API 响应中提取
- 存储在 `TokenUsageInfo` 中
- 用于触发 Compact（压缩）决策

---

## 8. 数据流转图

```
┌────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                    │
└────────────────────────────────────────────────────────────────────────┘

用户输入 (TUI/CLI)
       │
       ▼
┌─────────────────┐
│   Submission    │  ──▶  codex.rs:264 (Codex.tx_sub)
│   (用户消息)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  SessionState   │  ──▶  state/session.rs:16
│  加载历史记录   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  TurnContext    │  ──▶  codex.rs:919
│  构建回合上下文 │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Prompt        │  ──▶  client_common.rs
│   (系统+历史+输入)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ModelClient     │  ──▶  client.rs:150
│ ::stream()      │     WebSocket/SSE 流式请求
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 模型响应流      │
│ - 文本增量      │
│ - 工具调用请求  │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐  ┌──────────┐
│ 文本   │  │ Tool Call │
│ 输出   │  └────┬──────┘
└────────┘       │
                 ▼
        ┌─────────────────┐
        │ ToolRegistry    │  ──▶  tools/registry.rs:57
        │ ::dispatch()    │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ 工具执行        │
        │ (Sandboxed)     │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ ToolOutput      │
        │ 转换为响应项    │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ 发送到模型      │
        │ (下一轮迭代)    │
        └─────────────────┘
```

### 关键数据结构定义

```rust
// 事件类型 (protocol/protocol.rs)
pub struct Event {
    pub id: String,
    pub msg: EventMsg,
}

pub enum EventMsg {
    ItemStarted(ItemStartedEvent),
    ItemCompleted(ItemCompletedEvent),
    ResponseOutputItemDelta(...),
    ToolCallRequest(ToolCallRequest),
    ExecApprovalRequest(ExecApprovalRequestEvent),
    // ...
}

// 工具调用 (tools/context.rs)
pub struct ToolInvocation {
    pub session: Weak<Session>,
    pub turn: Arc<TurnContext>,
    pub tool_name: Arc<str>,
    pub call_id: Arc<str>,
    pub payload: ToolPayload,
}

// 模型请求 (client_common.rs)
pub struct Prompt {
    pub base_instructions: BaseInstructions,
    pub input: Vec<ResponseInputItem>,
    pub tools: Vec<Tool>,
    pub parallel_tool_calls: bool,
}
```

---

## 9. 源码索引

### 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `codex/codex-rs/cli/src/main.rs` | 545 | main() 函数 |
| Codex 主结构 | `codex/codex-rs/core/src/codex.rs` | 266 | Codex struct |
| Session 管理 | `codex/codex-rs/core/src/codex.rs` | 806 | Session impl |
| TurnContext | `codex/codex-rs/core/src/codex.rs` | 919 | make_turn_context() |
| SessionState | `codex/codex-rs/core/src/state/session.rs` | 16 | SessionState struct |
| 工具注册表 | `codex/codex-rs/core/src/tools/registry.rs` | 57 | ToolRegistry |
| 工具 Trait | `codex/codex-rs/core/src/tools/registry.rs` | 32 | ToolHandler trait |
| 模型客户端 | `codex/codex-rs/core/src/client.rs` | 150 | ModelClient |
| 流式请求 | `codex/codex-rs/core/src/client.rs` | 887 | stream() 方法 |
| 响应流映射 | `codex/codex-rs/core/src/client.rs` | 1002 | map_response_stream() |

### 子命令实现

| 命令 | 文件路径 | 行号 |
|------|----------|------|
| exec | `codex/codex-rs/exec/src/lib.rs` | - |
| review | `codex/codex-rs/exec/src/lib.rs` | - |
| mcp | `codex/codex-rs/cli/src/mcp_cmd.rs` | - |
| resume | `codex/codex-rs/cli/src/main.rs` | 634-650 |
| fork | `codex/codex-rs/cli/src/main.rs` | 651-667 |

### 工具实现

| 工具类型 | 文件路径 |
|----------|----------|
| Shell | `core/src/tools/handlers/shell.rs` |
| File | `core/src/tools/handlers/file.rs` |
| Search | `core/src/tools/handlers/search.rs` |
| Web Search | `core/src/tools/handlers/web_search.rs` |
| Apply Patch | `core/src/tools/handlers/apply_patch.rs` |
| MCP 工具 | `core/src/mcp/` |

---

## 总结

Codex 是一个架构清晰、注重安全的 Rust CLI Agent：

1. **分层架构** - CLI → TUI → Core → Session → Tools → Client
2. **安全优先** - 多层沙箱、审批机制、变更性操作控制
3. **流式处理** - 支持 WebSocket 和 SSE，优先 WebSocket
4. **状态持久化** - RolloutRecorder 支持会话恢复和分叉
5. **扩展能力** - MCP 协议支持外部工具集成
