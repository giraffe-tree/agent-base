# Tool System（codex）

本文基于 `./codex/codex-rs/core/src/tools` 源码，解释 codex 的工具系统架构——从 ToolSpec 定义、Registry 注册到 Router 调度的完整链路。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────────┐
│  配置层：ToolsConfig 定义工具集                                       │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolsConfig                                                    ││
│  │  ├── shell_type                   Shell 工具类型                 ││
│  │  │   ├── Disabled                 禁用                           ││
│  │  │   ├── ShellCommand            命令执行                        ││
│  │  │   └── UnifiedExec             统一执行(PTY)                  ││
│  │  ├── apply_patch_tool_type        补丁工具类型                   ││
│  │  │   ├── Freeform                自由格式                       ││
│  │  │   └── Function                函数调用格式                   ││
│  │  ├── web_search_mode             搜索模式                       ││
│  │  ├── js_repl_enabled             JS REPL 支持                   ││
│  │  ├── collab_tools                协作工具                       ││
│  │  └── experimental_supported_tools 实验性工具                    ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  注册层：ToolRegistry 管理工具 Handler                                │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolRegistry                                                   ││
│  │  ├── handlers: HashMap<String, Arc<dyn ToolHandler>>            ││
│  │  │                                                             ││
│  │  ├── dispatch(invocation)       分发工具调用                    ││
│  │  │   ├── 查找 handler                                          ││
│  │  │   ├── 检查 mutating → 等待 tool_call_gate                   ││
│  │  │   ├── 执行 handle(invocation)                               ││
│  │  │   ├── 触发 after_tool_use hook                              ││
│  │  │   └── 返回 ResponseInputItem                                ││
│  │  │                                                             ││
│  │  └── handler(name)              获取工具处理器                  ││
│  │                                                             ││
│  │  ToolRegistryBuilder                                           ││
│  │  ├── push_spec()                添加工具定义                    ││
│  │  ├── register_handler()         注册处理器                      ││
│  │  └── build()                    构建 Registry                   ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  路由层：ToolRouter 处理工具调用解析与分发                              │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolRouter                                                     ││
│  │  ├── from_config()               从配置创建                     ││
│  │  │   └── build_specs()           构建工具定义列表               ││
│  │  ├── build_tool_call()           解析 ResponseItem → ToolCall   ││
│  │  │   ├── FunctionCall            函数调用                       ││
│  │  │   ├── CustomToolCall          自定义工具                     ││
│  │  │   ├── LocalShellCall          本地 Shell                     ││
│  │  │   └── MCP 工具解析                                           ││
│  │  └── dispatch_tool_call()        分发执行                       ││
│  │      ├── js_repl_tools_only 检查                               ││
│  │      └── registry.dispatch()                                   ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念与设计哲学

### 2.1 一句话定义

codex 的工具系统是「**配置驱动 + Handler 注册 + 统一调度**」的架构：通过 `ToolsConfig` 定义工具集配置，`ToolRegistry` 管理工具 Handler 的注册与执行，`ToolRouter` 负责从模型输出解析工具调用并分发执行。

### 2.2 设计特点

| 特性 | 实现方式 | 优势 |
|------|---------|------|
| 配置驱动 | `ToolsConfig` 集中管理 | 模型适配、Feature 控制 |
| Handler 模式 | `ToolHandler` trait | 统一接口，易于扩展 |
| 多种 Payload | `ToolPayload` enum | 支持 Function/Custom/LocalShell/MCP |
| 变异检测 | `is_mutating()` | 精细化并发控制 |
| Hook 集成 | `after_tool_use` | 可插拔的扩展机制 |
| Telemetry | 自动记录工具调用 | 可观测性 |

---

## 3. 工具配置：ToolsConfig

### 3.1 配置结构

```rust
// codex-rs/core/src/tools/spec.rs
pub(crate) struct ToolsConfig {
    pub shell_type: ConfigShellToolType,              // Shell 执行方式
    pub apply_patch_tool_type: Option<ApplyPatchToolType>, // 补丁工具格式
    pub web_search_mode: Option<WebSearchMode>,       // 搜索模式
    pub agent_roles: BTreeMap<String, AgentRoleConfig>, // Agent 角色
    pub search_tool: bool,                            // 是否启用搜索
    pub js_repl_enabled: bool,                        // JS REPL
    pub js_repl_tools_only: bool,                     // 仅 JS REPL 工具
    pub collab_tools: bool,                           // 协作工具
    pub collaboration_modes_tools: bool,              // 协作模式
    pub experimental_supported_tools: Vec<String>,    // 实验性工具
}
```

### 3.2 Shell 工具类型

```rust
pub enum ConfigShellToolType {
    Disabled,           // 禁用 Shell 工具
    ShellCommand,       // 使用 shell 命令执行
    UnifiedExec,        // 使用统一执行器(PTY 支持)
}
```

类型选择逻辑：
- Feature::ShellTool 禁用 → Disabled
- Feature::ShellZshFork 启用 → ShellCommand
- Feature::UnifiedExec 启用 + ConPTY 支持 → UnifiedExec
- 默认 → 跟随 model_info.shell_type

### 3.3 Apply Patch 工具类型

```rust
pub enum ApplyPatchToolType {
    Freeform,   // 自由格式：纯文本补丁
    Function,   // 函数格式：结构化参数
}
```

---

## 4. 工具注册：ToolRegistry

### 4.1 核心结构

```rust
// codex-rs/core/src/tools/registry.rs
pub struct ToolRegistry {
    handlers: HashMap<String, Arc<dyn ToolHandler>>,
}

pub struct ToolRegistryBuilder {
    handlers: HashMap<String, Arc<dyn ToolHandler>>,
    specs: Vec<ConfiguredToolSpec>,
}

pub struct ConfiguredToolSpec {
    pub spec: ToolSpec,
    pub supports_parallel_tool_calls: bool,
}
```

### 4.2 ToolHandler Trait

```rust
#[async_trait]
pub trait ToolHandler: Send + Sync {
    fn kind(&self) -> ToolKind;

    /// 检查调用是否与 Handler 类型匹配
    fn matches_kind(&self, payload: &ToolPayload) -> bool {
        matches!(
            (self.kind(), payload),
            (ToolKind::Function, ToolPayload::Function { .. })
                | (ToolKind::Mcp, ToolPayload::Mcp { .. })
        )
    }

    /// 判断工具调用是否可能改变环境（用于并发控制）
    async fn is_mutating(&self, _invocation: &ToolInvocation) -> bool {
        false
    }

    /// 执行工具调用
    async fn handle(&self, invocation: ToolInvocation) -> Result<ToolOutput, FunctionCallError>;
}

pub enum ToolKind {
    Function,
    Mcp,
}
```

### 4.3 工具分发流程

```rust
impl ToolRegistry {
    pub async fn dispatch(
        &self,
        invocation: ToolInvocation,
    ) -> Result<ResponseInputItem, FunctionCallError> {
        let tool_name = invocation.tool_name.clone();

        // 1. 查找 Handler
        let handler = match self.handler(tool_name.as_ref()) {
            Some(handler) => handler,
            None => return Err(FunctionCallError::RespondToModel(...)),
        };

        // 2. 检查 Payload 类型匹配
        if !handler.matches_kind(&invocation.payload) {
            return Err(FunctionCallError::Fatal(...));
        }

        // 3. 检查是否为变异操作
        let is_mutating = handler.is_mutating(&invocation).await;

        // 4. 如果是变异操作，等待 tool_call_gate
        if is_mutating {
            invocation.turn.tool_call_gate.wait_ready().await;
        }

        // 5. 执行工具
        let result = handler.handle(invocation).await;

        // 6. 触发 after_tool_use hook
        let hook_abort_error = dispatch_after_tool_use_hook(...).await;

        // 7. 返回结果
        match result {
            Ok(output) => Ok(output.into_response(&call_id_owned, &payload_for_response)),
            Err(err) => Err(err),
        }
    }
}
```

### 4.4 变异操作控制

```rust
let is_mutating = handler.is_mutating(&invocation).await;
let output_cell = tokio::sync::Mutex::new(None);

let result = otel
    .log_tool_result_with_tags(..., || async move {
        if is_mutating {
            tracing::trace!("waiting for tool gate");
            invocation_for_tool.turn.tool_call_gate.wait_ready().await;
            tracing::trace!("tool gate released");
        }
        handler.handle(invocation_for_tool).await
    })
    .await;
```

`tool_call_gate` 用于控制变异工具的并发执行，确保线程安全。

---

## 5. 工具路由：ToolRouter

### 5.1 核心结构

```rust
// codex-rs/core/src/tools/router.rs
pub struct ToolRouter {
    registry: ToolRegistry,
    specs: Vec<ConfiguredToolSpec>,
}

pub struct ToolCall {
    pub tool_name: String,
    pub call_id: String,
    pub payload: ToolPayload,
}

pub enum ToolCallSource {
    Direct,
    JsRepl,
}
```

### 5.2 从配置创建

```rust
impl ToolRouter {
    pub fn from_config(
        config: &ToolsConfig,
        mcp_tools: Option<HashMap<String, Tool>>,      // MCP 工具
        app_tools: Option<HashMap<String, ToolInfo>>,  // App 连接器工具
        dynamic_tools: &[DynamicToolSpec],              // 动态工具
    ) -> Self {
        let builder = build_specs(config, mcp_tools, app_tools, dynamic_tools);
        let (specs, registry) = builder.build();
        Self { registry, specs }
    }

    pub fn specs(&self) -> Vec<ToolSpec> {
        self.specs.iter().map(|c| c.spec.clone()).collect()
    }

    pub fn tool_supports_parallel(&self, tool_name: &str) -> bool {
        self.specs
            .iter()
            .filter(|c| c.supports_parallel_tool_calls)
            .any(|c| c.spec.name() == tool_name)
    }
}
```

### 5.3 工具调用解析

```rust
pub async fn build_tool_call(
    session: &Session,
    item: ResponseItem,
) -> Result<Option<ToolCall>, FunctionCallError> {
    match item {
        // 1. 函数调用
        ResponseItem::FunctionCall { name, arguments, call_id, .. } => {
            // 检查是否为 MCP 工具
            if let Some((server, tool)) = session.parse_mcp_tool_name(&name).await {
                Ok(Some(ToolCall {
                    tool_name: name,
                    call_id,
                    payload: ToolPayload::Mcp { server, tool, raw_arguments: arguments },
                }))
            } else {
                Ok(Some(ToolCall {
                    tool_name: name,
                    call_id,
                    payload: ToolPayload::Function { arguments },
                }))
            }
        }

        // 2. 自定义工具调用
        ResponseItem::CustomToolCall { name, input, call_id, .. } => {
            Ok(Some(ToolCall {
                tool_name: name,
                call_id,
                payload: ToolPayload::Custom { input },
            }))
        }

        // 3. 本地 Shell 调用
        ResponseItem::LocalShellCall { id, call_id, action, .. } => {
            let call_id = call_id.or(id).ok_or(FunctionCallError::MissingLocalShellCallId)?;
            match action {
                LocalShellAction::Exec(exec) => {
                    let params = ShellToolCallParams {
                        command: exec.command,
                        workdir: exec.working_directory,
                        timeout_ms: exec.timeout_ms,
                        sandbox_permissions: Some(SandboxPermissions::UseDefault),
                        prefix_rule: None,
                        justification: None,
                    };
                    Ok(Some(ToolCall {
                        tool_name: "local_shell".to_string(),
                        call_id,
                        payload: ToolPayload::LocalShell { params },
                    }))
                }
            }
        }

        _ => Ok(None),
    }
}
```

### 5.4 工具分发执行

```rust
pub async fn dispatch_tool_call(
    &self,
    session: Arc<Session>,
    turn: Arc<TurnContext>,
    tracker: SharedTurnDiffTracker,
    call: ToolCall,
    source: ToolCallSource,
) -> Result<ResponseInputItem, FunctionCallError> {
    let ToolCall { tool_name, call_id, payload } = call;
    let payload_outputs_custom = matches!(payload, ToolPayload::Custom { .. });

    // 1. js_repl_tools_only 模式检查
    if source == ToolCallSource::Direct
        && turn.tools_config.js_repl_tools_only
        && !matches!(tool_name.as_str(), "js_repl" | "js_repl_reset")
    {
        return Ok(Self::failure_response(
            failure_call_id,
            payload_outputs_custom,
            FunctionCallError::RespondToModel(
                "direct tool calls are disabled; use js_repl and codex.tool(...) instead".to_string()
            ),
        ));
    }

    // 2. 构建调用上下文
    let invocation = ToolInvocation {
        session,
        turn,
        tracker,
        call_id,
        tool_name,
        payload,
    };

    // 3. 通过 Registry 分发
    match self.registry.dispatch(invocation).await {
        Ok(response) => Ok(response),
        Err(FunctionCallError::Fatal(message)) => Err(FunctionCallError::Fatal(message)),
        Err(err) => Ok(Self::failure_response(failure_call_id, payload_outputs_custom, err)),
    }
}
```

---

## 6. 工具调用上下文

### 6.1 ToolInvocation

```rust
// codex-rs/core/src/tools/context.rs
pub struct ToolInvocation {
    pub session: Arc<Session>,           // 会话上下文
    pub turn: Arc<TurnContext>,          // Turn 上下文
    pub tracker: SharedTurnDiffTracker,  // 变更追踪
    pub call_id: String,                 // 调用 ID
    pub tool_name: String,               // 工具名
    pub payload: ToolPayload,            // 调用负载
}
```

### 6.2 ToolPayload 类型

```rust
pub enum ToolPayload {
    Function {
        arguments: String,      // JSON 格式参数
    },
    Custom {
        input: String,          // 自由格式输入
    },
    LocalShell {
        params: ShellToolCallParams,  // Shell 调用参数
    },
    Mcp {
        server: String,         // MCP 服务器名
        tool: String,           // MCP 工具名
        raw_arguments: String,  // 原始参数
    },
}

impl ToolPayload {
    pub fn log_payload(&self) -> Cow<'_, str> {
        match self {
            ToolPayload::Function { arguments } => Cow::Borrowed(arguments),
            ToolPayload::Custom { input } => Cow::Borrowed(input),
            ToolPayload::LocalShell { params } => Cow::Owned(params.command.join(" ")),
            ToolPayload::Mcp { raw_arguments, .. } => Cow::Borrowed(raw_arguments),
        }
    }
}
```

### 6.3 ToolOutput 类型

```rust
pub enum ToolOutput {
    Function {
        body: FunctionCallOutputBody,
        success: Option<bool>,
    },
    Mcp {
        result: Result<CallToolResult, String>,
    },
}

impl ToolOutput {
    pub fn log_preview(&self) -> String {
        // 生成用于日志预览的截断内容
    }

    pub fn success_for_logging(&self) -> bool {
        match self {
            ToolOutput::Function { success, .. } => success.unwrap_or(true),
            ToolOutput::Mcp { result } => result.is_ok(),
        }
    }

    pub fn into_response(self, call_id: &str, payload: &ToolPayload) -> ResponseInputItem {
        // 转换为 ResponseInputItem
    }
}
```

---

## 7. Hook 集成

### 7.1 after_tool_use Hook

```rust
struct AfterToolUseHookDispatch<'a> {
    invocation: &'a ToolInvocation,
    output_preview: String,
    success: bool,
    executed: bool,
    duration: Duration,
    mutating: bool,
}

async fn dispatch_after_tool_use_hook(
    dispatch: AfterToolUseHookDispatch<'_>,
) -> Option<FunctionCallError> {
    let tool_input = HookToolInput::from(&invocation.payload);

    let hook_outcomes = session
        .hooks()
        .dispatch(HookPayload {
            session_id: session.conversation_id,
            cwd: turn.cwd.clone(),
            triggered_at: chrono::Utc::now(),
            hook_event: HookEvent::AfterToolUse {
                event: HookEventAfterToolUse {
                    turn_id: turn.sub_id.clone(),
                    call_id: invocation.call_id.clone(),
                    tool_name: invocation.tool_name.clone(),
                    tool_kind: hook_tool_kind(&tool_input),
                    tool_input,
                    executed: dispatch.executed,
                    success: dispatch.success,
                    duration_ms: u64::try_from(dispatch.duration.as_millis()).unwrap_or(u64::MAX),
                    mutating: dispatch.mutating,
                    sandbox: ...,
                    sandbox_policy: ...,
                    output_preview: dispatch.output_preview.clone(),
                },
            },
        })
        .await;

    // 处理 Hook 结果
    for hook_outcome in hook_outcomes {
        match hook_outcome.result {
            HookResult::Success => {}
            HookResult::FailedContinue(error) => { /* 记录警告，继续 */ }
            HookResult::FailedAbort(error) => { /* 返回错误，中止 */ }
        }
    }
    None
}
```

### 7.2 Hook 结果处理

| 结果类型 | 行为 |
|---------|------|
| `Success` | 正常继续 |
| `FailedContinue` | 记录警告，继续执行 |
| `FailedAbort` | 中止操作，返回错误 |

---

## 8. 与其他组件的交互

### 8.1 与 Agent Loop 的交互

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Agent Loop  │────▶│ ToolRouter  │────▶│  LLM API    │
│             │     │   .specs()  │     │(ToolSpecs)  │
└──────┬──────┘     └──────┬──────┘     └─────────────┘
       │                   │
       │ ◄─────────────────┘
       │   ResponseItem (工具调用)
       ▼
┌─────────────────┐
│ build_tool_call │
│   ResponseItem  │
│     ──► ToolCall│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ dispatch_tool   │
│ _call()         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ToolRegistry    │
│  .dispatch()    │
│                 │
│ • is_mutating?  │
│ • tool_call_gate│
│ • handler.handle│
│ • after_tool_use│
│ hook            │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ResponseInputItem│
│ (返回给 LLM)    │
└─────────────────┘
```

### 8.2 与 MCP 的集成

```rust
// MCP 工具名解析
if let Some((server, tool)) = session.parse_mcp_tool_name(&name).await {
    return Ok(Some(ToolCall {
        tool_name: name,
        call_id,
        payload: ToolPayload::Mcp { server, tool, raw_arguments: arguments },
    }));
}
```

MCP 工具通过 `ToolPayload::Mcp` 类型处理，由专门的 MCP Handler 执行。

---

## 9. 架构特点总结

- **配置驱动**: `ToolsConfig` 集中管理工具集，支持模型适配
- **Handler 模式**: `ToolHandler` trait 提供统一的工具实现接口
- **多种 Payload**: `ToolPayload` enum 支持 Function/Custom/LocalShell/MCP 四种类型
- **变异检测**: `is_mutating()` + `tool_call_gate` 实现精细化并发控制
- **Hook 扩展**: `after_tool_use` hook 支持可插拔的后处理逻辑
- **Telemetry 集成**: 自动记录工具调用和结果预览

---

## 10. 排障速查

- **工具未找到**: 检查 `ToolRegistry` 中是否正确注册 handler
- **Payload 类型不匹配**: 检查 `matches_kind()` 实现
- **变异工具执行阻塞**: 查看 `tool_call_gate` 状态
- **Hook 中止**: 检查 `dispatch_after_tool_use_hook` 返回的错误
- **MCP 工具解析失败**: 检查 `parse_mcp_tool_name` 的服务器/工具名解析
- **js_repl_tools_only 阻止**: 确认调用 source 是否为 `ToolCallSource::JsRepl`
