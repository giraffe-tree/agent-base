# Tool System（codex）

> 📋 **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 25-35 分钟 |
> | 前置文档 | `04-codex-agent-loop.md`、`06-codex-mcp-integration.md` |
> | 文档结构 | 速览 → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

一句话定义：Codex 的 Tool System 是**配置驱动的工具注册与调度框架**，实现从模型输出到工具执行的完整链路。

Codex 的核心取舍：**配置驱动 + Handler 注册 + 统一调度**（对比 Gemini CLI 的 Zod Schema 定义、Kimi CLI 的 YAML 配置）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 核心机制 | Trait-based Handler 注册与调度 | `codex-rs/core/src/tools/registry.rs:163` |
| 并发控制 | 变异检测 + tool_call_gate 门控 | `codex-rs/core/src/tools/registry.rs:212` |
| 工具定义 | Rust struct + ToolsConfig 配置 | `codex-rs/core/src/tools/spec.rs:97` |
| MCP 集成 | 原生支持，自动解析 mcp__server__tool 格式 | `codex-rs/core/src/tools/router.rs:310` |
| 批量处理 | Agent Jobs + SQLite 持久化 | `codex-rs/core/src/tools/handlers/agent_jobs.rs:34` |

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

没有 Tool System：模型输出工具调用指令 → 需要手动解析 → 手动执行 → 手动格式化结果返回

有 Tool System：
```
模型输出: {"name": "shell", "arguments": "{\"command\": \"ls\"}"}
  ↓ ToolRouter 自动解析为 ToolCall
  ↓ ToolRegistry 查找对应 Handler
  ↓ ShellHandler 执行命令
  ↓ 结果自动格式化为 ResponseInputItem 返回给模型
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 工具发现 | 模型不知道有哪些工具可用 |
| 调用解析 | 模型输出格式不统一，难以解析 |
| 并发控制 | 变异工具并发执行导致数据竞争 |
| 扩展性 | 新增工具需要修改核心代码 |
| 可观测性 | 无法追踪工具调用链 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Runtime                                 │
│ codex-rs/core/src/loop.rs                                    │
└───────────────────────┬─────────────────────────────────────┘
                        │ 调用 ToolRouter
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Tool System ▓▓▓                                         │
│ codex-rs/core/src/tools/                                     │
│ - ToolRouter    : 工具调用解析与分发                         │
│ - ToolRegistry  : Handler 注册与执行                         │
│ - ToolSpec      : 工具定义与配置                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Shell Handler│ │ MCP Handler  │ │ File Handler │
│ 命令执行     │ │ 外部工具     │ │ 文件操作     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `ToolRouter` | 工具调用解析与分发入口 | `codex-rs/core/src/tools/router.rs:1` |
| `ToolRegistry` | Handler 注册管理与执行调度 | `codex-rs/core/src/tools/registry.rs:1` |
| `ToolHandler` | 工具执行抽象接口 | `codex-rs/core/src/tools/registry.rs:163` |
| `ToolsConfig` | 工具集配置定义 | `codex-rs/core/src/tools/spec.rs:97` |
| `ToolInvocation` | 工具调用上下文 | `codex-rs/core/src/tools/context.rs:424` |
| **BatchJobHandler** | **Agent Jobs 批量处理 Handler** | **`codex-rs/core/src/tools/handlers/agent_jobs.rs:34`** |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant R as ToolRouter
    participant P as ToolPayload
    participant G as ToolRegistry
    participant H as ToolHandler

    A->>R: build_tool_call(ResponseItem)
    Note over R: 解析模型输出
    R->>P: 提取 tool_name + arguments
    R->>G: dispatch_tool_call(ToolCall)
    G->>G: 查找 Handler
    G->>G: is_mutating()? 检查
    G->>G: wait_ready() 等待门控
    G->>H: handle(ToolInvocation)
    H-->>G: ToolOutput
    G->>G: after_tool_use Hook
    G-->>R: ResponseInputItem
    R-->>A: 返回结果
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent Loop 发起工具调用解析 | 统一入口，解耦模型输出格式 |
| 2 | 解析为结构化 ToolCall | 标准化内部表示 |
| 3 | Registry 分发执行 | 集中管理所有工具 Handler |
| 4-5 | 变异检测与门控等待 | 确保写操作线程安全 |
| 6 | Handler 执行具体逻辑 | 职责分离，易于扩展 |
| 7 | Hook 触发 | 可插拔的后处理机制 |
| 8 | 返回标准化响应 | 统一格式，便于 LLM 消费 |

---

## 3. 核心组件详细分析

### 3.1 ToolRouter 内部结构

#### 职责定位

ToolRouter 是 Tool System 的入口，负责从模型输出解析工具调用，并分发到 Registry 执行。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Idle: 初始化
    Idle --> Parsing: 收到 ResponseItem
    Parsing --> Dispatching: 解析成功
    Parsing --> Failed: 解析失败
    Dispatching --> Completed: 分发成功
    Dispatching --> Failed: Handler 未找到
    Completed --> [*]
    Failed --> [*]
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Idle | 空闲等待 | 初始化完成 | 收到 ResponseItem |
| Parsing | 解析工具调用 | 收到模型输出 | 解析完成或失败 |
| Dispatching | 分发执行 | 解析为 ToolCall | 执行完成或失败 |
| Completed | 完成 | 成功返回结果 | 自动结束 |
| Failed | 失败 | 解析错误或执行错误 | 返回错误响应 |

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  输入层                                                      │
│  ├── ResponseItem::FunctionCall ──► 解析参数                 │
│  ├── ResponseItem::CustomToolCall ──► 自定义处理             │
│  └── ResponseItem::LocalShellCall ──► Shell 参数提取         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  处理层                                                      │
│  ├── 工具名解析: mcp__server__tool 格式处理                   │
│  ├── Payload 封装: ToolPayload::Function/Custom/LocalShell/Mcp│
│  └── 调用构建: ToolCall { name, call_id, payload }           │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层                                                      │
│  ├── dispatch_tool_call() 分发到 Registry                    │
│  └── 错误处理: failure_response() 格式化                     │
└─────────────────────────────────────────────────────────────┘
```

#### 关键算法逻辑

```mermaid
flowchart TD
    A[ResponseItem 输入] --> B{类型判断}
    B -->|FunctionCall| C[解析 MCP?]
    B -->|CustomToolCall| D[ToolPayload::Custom]
    B -->|LocalShellCall| E[解析 Shell 参数]

    C -->|是| F[ToolPayload::Mcp]
    C -->|否| G[ToolPayload::Function]

    F --> H[构建 ToolCall]
    G --> H
    D --> H
    E --> H

    H --> I{js_repl_tools_only?}
    I -->|是| J[检查白名单]
    I -->|否| K[dispatch_tool_call]
    J -->|允许| K
    J -->|拒绝| L[返回错误响应]

    K --> M[Registry.dispatch]

    style C fill:#90EE90
    style J fill:#FFD700
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `from_config()` | ToolsConfig + MCP tools | ToolRouter | 从配置构建 | `codex-rs/core/src/tools/router.rs:283` |
| `build_tool_call()` | ResponseItem | Option<ToolCall> | 解析工具调用 | `codex-rs/core/src/tools/router.rs:310` |
| `dispatch_tool_call()` | ToolCall + Session | ResponseInputItem | 分发执行 | `codex-rs/core/src/tools/router.rs:372` |
| `specs()` | - | Vec<ToolSpec> | 获取工具定义列表 | `codex-rs/core/src/tools/router.rs:295` |

### 3.2 ToolRegistry 内部结构

#### 职责定位

ToolRegistry 负责管理所有 ToolHandler 的注册，并执行工具调度的核心逻辑，包括变异检测和门控控制。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Ready: 初始化完成
    Ready --> Dispatching: 收到 dispatch 请求
    Dispatching --> Checking: 查找 Handler
    Checking --> Waiting: is_mutating() = true
    Checking --> Executing: is_mutating() = false
    Waiting --> Executing: tool_call_gate 释放
    Executing --> Hooking: Handler 执行完成
    Hooking --> Completed: after_tool_use 完成
    Completed --> Ready: 返回结果

    Checking --> Failed: Handler 未找到
    Executing --> Failed: 执行出错
    Failed --> [*]
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Ready | 就绪等待 | 初始化完成 | 收到 dispatch 请求 |
| Dispatching | 正在分发 | 收到工具调用 | 开始查找 Handler |
| Checking | 检查 Handler | 找到 Handler | 检查完成 |
| Waiting | 等待门控 | 变异操作需等待 | tool_call_gate 释放 |
| Executing | 执行中 | 门控通过或非变异 | 执行完成 |
| Hooking | 触发 Hook | 执行完成 | Hook 完成 |
| Completed | 完成 | Hook 完成 | 返回结果 |
| Failed | 失败 | Handler 未找到或执行错误 | 返回错误 |

#### 关键算法逻辑

```rust
// codex-rs/core/src/tools/registry.rs:194-231
pub async fn dispatch(&self, invocation: ToolInvocation) -> Result<...> {
    // 1. 查找 Handler
    let handler = self.handler(tool_name.as_ref())?;

    // 2. 类型匹配检查
    if !handler.matches_kind(&invocation.payload) {
        return Err(FunctionCallError::Fatal(...));
    }

    // 3. 变异检测
    let is_mutating = handler.is_mutating(&invocation).await;

    // 4. 变异操作门控
    if is_mutating {
        invocation.turn.tool_call_gate.wait_ready().await;
    }

    // 5. 执行工具
    let result = handler.handle(invocation).await;

    // 6. Hook 触发
    dispatch_after_tool_use_hook(...).await;

    result
}
```

**算法要点**：

1. **门控机制**：变异操作需等待 `tool_call_gate`，确保并发安全
2. **类型安全**：`matches_kind()` 在运行时验证 Handler 与 Payload 匹配
3. **Hook 扩展**：`after_tool_use` 支持可插拔的后处理逻辑
4. **错误分类**：`FunctionCallError` 区分 Fatal（终止）和 RespondToModel（可恢复）

### 3.3 组件间协作时序

```mermaid
sequenceDiagram
    participant U as Agent Loop
    participant R as ToolRouter
    participant S as Session
    participant G as ToolRegistry
    participant H as Handler
    participant O as Otel

    U->>R: dispatch_tool_call(call, session, turn)
    activate R

    R->>R: js_repl_tools_only 检查
    Note right of R: 安全策略检查

    R->>R: 构建 ToolInvocation
    Note right of R: 封装调用上下文

    R->>G: registry.dispatch(invocation)
    activate G

    G->>G: 查找 Handler
    G->>G: matches_kind() 验证
    G->>H: is_mutating(&invocation)
    activate H
    H-->>G: bool
    deactivate H

    alt is_mutating = true
        G->>G: tool_call_gate.wait_ready()
        Note right of G: 等待并发控制门控
    end

    G->>O: log_tool_result_with_tags()
    activate O
    O->>H: handle(invocation)
    activate H
    H->>S: 执行具体操作
    S-->>H: 执行结果
    H-->>O: ToolOutput
    deactivate H
    O-->>G: Result
    deactivate O

    G->>G: dispatch_after_tool_use_hook()
    G-->>R: ResponseInputItem
    deactivate G

    R-->>U: 返回结果
    deactivate R
```

**协作要点**：

1. **调用方与 ToolRouter**：Agent Loop 通过统一接口发起调用，解耦具体工具实现
2. **ToolRouter 与 ToolRegistry**：Router 负责解析和路由，Registry 负责执行调度
3. **ToolRegistry 与 Handler**：Registry 管理 Handler 生命周期，Handler 专注具体逻辑
4. **Handler 与外部服务**：通过 Session 访问文件系统、执行命令等

### 3.4 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[ResponseItem] --> I2[ToolRouter.build_tool_call]
        I2 --> I3[ToolCall]
    end

    subgraph Process["处理阶段"]
        P1[ToolRegistry.dispatch] --> P2[Handler.is_mutating]
        P2 --> P3[tool_call_gate.wait]
        P3 --> P4[Handler.handle]
        P4 --> P5[after_tool_use Hook]
    end

    subgraph Output["输出阶段"]
        O1[ToolOutput] --> O2[ResponseInputItem]
        O2 --> O3[返回 Agent Loop]
    end

    I3 --> P1
    P5 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[执行错误] --> E1{错误类型}
    E1 -->|Fatal| R1[终止整个 Turn]
    E1 -->|RespondToModel| R2[返回错误给模型]
    E1 -->|PayloadMismatch| R3[内部错误日志]

    R1 --> End[结束]
    R2 --> End
    R3 --> End

    style R1 fill:#FF6B6B
    style R2 fill:#FFD700
```

#### 优化路径（缓存/短路）

```mermaid
flowchart TD
    Start[请求进入] --> Check{js_repl_tools_only?}
    Check -->|是| Whitelist{在白名单?}
    Check -->|否| Normal[正常流程]
    Whitelist -->|是| Normal
    Whitelist -->|否| Reject[返回错误]
    Normal --> Result[返回结果]
    Reject --> Result

    style Whitelist fill:#90EE90
    style Reject fill:#FF6B6B
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant M as LLM Output
    participant R as ToolRouter
    participant G as ToolRegistry
    participant H as Handler
    participant C as Context

    M->>R: FunctionCall { name, arguments, call_id }
    R->>R: parse_mcp_tool_name()? 检查 MCP 格式
    R->>R: 构建 ToolPayload
    R->>R: 构建 ToolCall

    R->>G: dispatch_tool_call()
    G->>G: 查找 Handler
    G->>H: is_mutating()
    H-->>G: false/true

    G->>H: handle(ToolInvocation)
    H->>C: 执行工具逻辑
    C-->>H: 原始结果

    H->>H: 封装 ToolOutput
    H-->>G: ToolOutput::Function { body, success }

    G->>G: after_tool_use Hook
    G->>G: ToolOutput.into_response()
    G-->>R: ResponseInputItem::FunctionCallOutput

    R-->>M: 返回给 LLM
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 解析 | ResponseItem | 提取 name/args/call_id | ToolCall | `codex-rs/core/src/tools/router.rs:310` |
| 分发 | ToolCall | 查找 Handler + 变异检查 | ToolInvocation | `codex-rs/core/src/tools/router.rs:398` |
| 执行 | ToolInvocation | Handler 具体逻辑 | ToolOutput | `codex-rs/core/src/tools/registry.rs:220` |
| 响应 | ToolOutput | 格式化为模型输入 | ResponseInputItem | `codex-rs/core/src/tools/context.rs:491` |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph LLM["LLM 层"]
        L1[ResponseItem<br/>工具调用输出]
        L2[ResponseInputItem<br/>工具结果输入]
    end

    subgraph Router["Router 层"]
        R1[build_tool_call]
        R2[dispatch_tool_call]
    end

    subgraph Registry["Registry 层"]
        G1[dispatch]
        G2[Handler 查找]
        G3[变异控制]
    end

    subgraph Handler["Handler 层"]
        H1[is_mutating]
        H2[handle]
        H3[ToolOutput]
    end

    L1 --> R1
    R1 --> R2
    R2 --> G1
    G1 --> G2
    G2 --> G3
    G3 --> H1
    H1 --> H2
    H2 --> H3
    H3 --> G1
    G1 --> R2
    R2 --> L2
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始] --> B{Handler 存在?}
    B -->|否| C[返回 RespondToModel 错误]
    B -->|是| D{类型匹配?}
    D -->|否| E[返回 Fatal 错误]
    D -->|是| F{变异操作?}
    F -->|是| G[等待 tool_call_gate]
    F -->|否| H[直接执行]
    G --> I[执行 Handler]
    H --> I
    I --> J{执行结果?}
    J -->|成功| K[触发 after_tool_use]
    J -->|失败| L[返回错误]
    K --> M[返回 ResponseInputItem]
    C --> N[结束]
    E --> N
    L --> N
    M --> N
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```rust
// codex-rs/core/src/tools/context.rs:424-431
pub struct ToolInvocation {
    pub session: Arc<Session>,           // 会话上下文
    pub turn: Arc<TurnContext>,          // Turn 上下文
    pub tracker: SharedTurnDiffTracker,  // 变更追踪
    pub call_id: String,                 // 调用 ID
    pub tool_name: String,               // 工具名
    pub payload: ToolPayload,            // 调用负载
}

// codex-rs/core/src/tools/context.rs:437-452
pub enum ToolPayload {
    Function { arguments: String },
    Custom { input: String },
    LocalShell { params: ShellToolCallParams },
    Mcp { server: String, tool: String, raw_arguments: String },
}
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `session` | `Arc<Session>` | 共享会话状态 |
| `turn` | `Arc<TurnContext>` | 当前 Turn 上下文，包含 tool_call_gate |
| `tracker` | `SharedTurnDiffTracker` | 文件变更追踪 |
| `payload` | `ToolPayload` | 支持多种调用格式 |

### 5.2 主链路代码

**关键代码**（核心逻辑）：

```rust
// codex-rs/core/src/tools/registry.rs:194-231
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
        dispatch_after_tool_use_hook(...).await;

        // 7. 返回结果
        match result {
            Ok(output) => Ok(output.into_response(...)),
            Err(err) => Err(err),
        }
    }
}
```

**设计意图**：

1. **双层查找**：先查 Handler，再验证类型匹配，防止误调用
2. **变异门控**：`is_mutating()` + `tool_call_gate` 确保写操作串行化
3. **错误分类**：`RespondToModel` 可恢复错误返回给 LLM，`Fatal` 终止执行
4. **Hook 扩展**：`after_tool_use` 支持监控、审计等后处理

<details>
<summary>📋 查看完整实现（含错误处理、日志等）</summary>

```rust
// codex-rs/core/src/tools/registry.rs:194-250
pub async fn dispatch(
    &self,
    invocation: ToolInvocation,
) -> Result<ResponseInputItem, FunctionCallError> {
    let tool_name = invocation.tool_name.clone();

    // 查找 Handler
    let handler = match self.handler(tool_name.as_ref()) {
        Some(handler) => handler,
        None => {
            return Err(FunctionCallError::RespondToModel(format!(
                "Tool '{}' not found",
                tool_name
            )));
        }
    };

    // 检查 Payload 类型匹配
    if !handler.matches_kind(&invocation.payload) {
        return Err(FunctionCallError::Fatal(format!(
            "Payload mismatch for tool '{}'",
            tool_name
        )));
    }

    // 检查是否为变异操作
    let is_mutating = handler.is_mutating(&invocation).await;

    // 如果是变异操作，等待 tool_call_gate
    if is_mutating {
        invocation.turn.tool_call_gate.wait_ready().await;
    }

    // 执行工具
    let result = handler.handle(invocation).await;

    // 触发 after_tool_use hook
    if let Err(e) = dispatch_after_tool_use_hook(...).await {
        tracing::warn!("after_tool_use hook failed: {}", e);
    }

    // 返回结果
    match result {
        Ok(output) => Ok(output.into_response(...)),
        Err(err) => Err(err),
    }
}
```

</details>

### 5.3 关键调用链

```text
dispatch_tool_call()      [codex-rs/core/src/tools/router.rs:372]
  -> ToolRegistry::dispatch()    [codex-rs/core/src/tools/registry.rs:194]
    -> handler.is_mutating()     [codex-rs/core/src/tools/registry.rs:212]
      - ShellHandler: 检测命令是否为写操作
    -> tool_call_gate.wait_ready()  [codex-rs/core/src/turn.rs:?]
      - 等待并发控制信号
    -> handler.handle()          [codex-rs/core/src/tools/handlers/*.rs]
      - ShellHandler::handle()   [codex-rs/core/src/tools/handlers/shell.rs:?]
      - McpHandler::handle()     [codex-rs/core/src/tools/handlers/mcp.rs:256]
      - FileHandler::handle()    [codex-rs/core/src/tools/handlers/file.rs:?]
```

---

## 6. Agent Jobs 批量处理系统

### 6.1 功能概述

Agent Jobs 是 Codex 新增的**批量处理工作流系统**，支持 Map-Reduce 风格的任务分发与结果收集。核心能力包括：

- **CSV 批量处理**：`spawn_agents_on_csv` 工具读取 CSV，每行生成一个子 Agent 任务
- **自动并发控制**：默认 16 并发，最大支持 64，自动管理子 Agent 生命周期
- **SQLite 持久化**：任务状态、进度、结果存储在 SQLite，支持断点续传
- **进度条 UI**：exec 模式下实时显示处理进度和 ETA

### 6.2 架构位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Runtime                                 │
│  - 调用 spawn_agents_on_csv                                  │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Agent Jobs System ▓▓▓                                   │
│ codex-rs/core/src/tools/handlers/agent_jobs.rs               │
│ - BatchJobHandler       : 工具 Handler 入口                  │
│ - spawn_agents_on_csv   : CSV 解析 + 任务创建                │
│ - report_agent_job_result: 子 Agent 结果上报                 │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ StateRuntime │ │ Multi-Agent  │ │ Exec Progress│
│ SQLite 存储  │ │ 子 Agent 创建 │ │ 进度条 UI    │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 6.3 数据模型

```rust
// codex-rs/state/src/model/agent_job.rs:74-92
pub struct AgentJob {
    pub id: String,
    pub name: String,
    pub status: AgentJobStatus,  // Pending/Running/Completed/Failed/Cancelled
    pub instruction: String,       // 任务指令模板
    pub auto_export: bool,         // 自动导出结果到 CSV
    pub max_runtime_seconds: Option<u64>,
    pub output_schema_json: Option<Value>,
    pub input_headers: Vec<String>,
    pub input_csv_path: String,
    pub output_csv_path: String,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

pub struct AgentJobItem {
    pub job_id: String,
    pub item_id: String,
    pub row_index: i64,
    pub source_id: Option<String>,
    pub row_json: Value,           // CSV 行数据
    pub status: AgentJobItemStatus,
    pub assigned_thread_id: Option<String>,
    pub attempt_count: i64,
    pub result_json: Option<Value>,
}
```

### 6.4 核心实现

```rust
// codex-rs/core/src/tools/handlers/agent_jobs.rs:34-40
pub struct BatchJobHandler;

const DEFAULT_AGENT_JOB_CONCURRENCY: usize = 16;
const MAX_AGENT_JOB_CONCURRENCY: usize = 64;
const STATUS_POLL_INTERVAL: Duration = Duration::from_millis(250);
const PROGRESS_EMIT_INTERVAL: Duration = Duration::from_secs(1);

// codex-rs/core/src/tools/handlers/agent_jobs.rs:176-211
#[async_trait]
impl ToolHandler for BatchJobHandler {
    fn kind(&self) -> ToolKind {
        ToolKind::Function
    }

    async fn handle(&self, invocation: ToolInvocation) -> Result<ToolOutput, FunctionCallError> {
        // 根据工具名分发到具体实现
        match tool_name.as_str() {
            "spawn_agents_on_csv" => spawn_agents_on_csv::handle(session, turn, arguments).await,
            "report_agent_job_result" => report_agent_job_result::handle(session, arguments).await,
            other => Err(FunctionCallError::RespondToModel(...)),
        }
    }
}
```

### 6.5 Map-Reduce 工作流

```mermaid
sequenceDiagram
    participant M as Main Agent
    participant S as spawn_agents_on_csv
    participant DB as SQLite
    participant W as Worker Agents
    participant R as report_agent_job_result

    M->>S: 调用 spawn_agents_on_csv(csv_path, instruction)
    S->>DB: 创建 AgentJob + AgentJobItems
    S->>S: 启动并发调度器
    loop 每 250ms 轮询
        S->>DB: 查询待处理 items
        S->>W: 为每个 item 创建子 Agent
        W->>W: 执行 instruction 模板
        W->>R: 调用 report_agent_job_result
        R->>DB: 写入 result_json
    end
    S->>DB: 标记 Job 完成
    S->>S: 导出结果到 output_csv_path
    S-->>M: 返回 SpawnAgentsOnCsvResult
```

### 6.6 进度条 UI 实现

```rust
// codex-rs/exec/src/event_processor_with_human_output.rs:968-990
fn render_agent_job_progress(&mut self, update: AgentJobProgressMessage) {
    let total = update.total_items.max(1);
    let processed = update.completed_items + update.failed_items;
    let percent = (processed as f64 / total as f64 * 100.0).round() as i64;
    let job_label = update.job_id.chars().take(8).collect::<String>();
    let eta = update.eta_seconds
        .map(|secs| format!("{}m{:02}s", secs / 60, secs % 60))
        .unwrap_or_else(|| "--".to_string());

    let line = format_agent_job_progress_line(
        columns, job_label.as_str(), stats, eta.as_str(),
    );
    // 渲染进度条到终端
}
```

### 6.7 配置与启用

Agent Jobs 需要同时满足以下条件才会启用：

```rust
// codex-rs/core/src/tools/spec.rs:87
let include_agent_jobs = include_collab_tools && features.enabled(Feature::Sqlite);

// codex-rs/core/src/tools/spec.rs:1839-1846
if config.agent_jobs_tools {
    let agent_jobs_handler = Arc::new(BatchJobHandler);
    builder.push_spec(create_spawn_agents_on_csv_tool());
    builder.register_handler("spawn_agents_on_csv", agent_jobs_handler.clone());
    if config.agent_jobs_worker_tools {
        builder.push_spec(create_report_agent_job_result_tool());
        builder.register_handler("report_agent_job_result", agent_jobs_handler);
    }
}
```

### 6.8 数据库 Schema

```sql
-- codex-rs/state/migrations/0014_agent_jobs.sql
CREATE TABLE agent_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    instruction TEXT NOT NULL,
    output_schema_json TEXT,
    input_headers_json TEXT NOT NULL,
    input_csv_path TEXT NOT NULL,
    output_csv_path TEXT NOT NULL,
    auto_export INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    last_error TEXT
);

CREATE TABLE agent_job_items (
    job_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    source_id TEXT,
    row_json TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_thread_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    PRIMARY KEY (job_id, item_id),
    FOREIGN KEY(job_id) REFERENCES agent_jobs(id) ON DELETE CASCADE
);
```

---

## 7. 设计意图与 Trade-off

### 7.1 Codex 的选择

| 维度 | Codex 的选择 | 替代方案 | 取舍分析 |
|-----|-------------|---------|---------|
| 工具定义 | Rust struct + ToolsConfig | Zod Schema (Gemini) / YAML (Kimi) | 编译期类型安全，但需要重新编译 |
| Handler 模式 | Trait-based 注册 | 函数映射表 / 反射调用 | 统一接口，易于单元测试 |
| 并发控制 | tool_call_gate 门控 | 无控制 (Gemini) / 完全串行 | 读操作并行，写操作串行 |
| MCP 支持 | 原生集成 | 插件化 (Kimi) | 开箱即用，但增加核心复杂度 |
| 错误处理 | 分层错误类型 | 统一错误码 | 精确区分可恢复/致命错误 |
| **批量处理** | **Agent Jobs + SQLite** | **无 / 外部队列** | **内置持久化，支持断点续传** |

### 7.2 为什么这样设计？

**核心问题**：如何在保证安全的前提下，支持灵活的扩展和高效的并发？

**Codex 的解决方案**：
- 代码依据：`codex-rs/core/src/tools/registry.rs:212-217` 的 `is_mutating()` 检查 + `tool_call_gate.wait_ready()`
- 设计意图：通过声明式的变异检测，让 Handler 决定是否需要串行执行
- 带来的好处：
  - 读操作可以并行（如多个文件读取）
  - 写操作自动串行（避免数据竞争）
  - Handler 可自定义变异判断逻辑（如某些 shell 命令是只读的）
- 付出的代价：
  - Handler 需要正确实现 `is_mutating()`
  - 门控增加了调用延迟

**Agent Jobs 设计意图**：
- 代码依据：`codex-rs/core/src/tools/handlers/agent_jobs.rs:36-40` 的并发常量和 `codex-rs/state/src/runtime/agent_jobs.rs` 的状态管理
- 设计意图：内置 Map-Reduce 工作流，无需依赖外部任务队列
- 带来的好处：
  - SQLite 持久化支持断点续传和故障恢复
  - 自动并发控制避免资源耗尽
  - 进度条 UI 提供实时可视反馈
- 付出的代价：
  - 依赖 SQLite 功能特性
  - 子 Agent 创建受线程深度限制

### 7.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统方案"
    branch "Codex"
    checkout "Codex"
    commit id: "Trait-based + 门控"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "Zod Schema"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "YAML 配置"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "插件化系统"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| Codex | Trait-based Handler + 变异门控 + Agent Jobs | 需要精细并发控制和批量处理的场景 |
| Gemini CLI | Zod Schema 定义工具，TypeScript 类型驱动 | 快速原型，TypeScript 生态 |
| Kimi CLI | YAML 配置 + 命令映射，简单直观 | 简单工具，快速扩展 |
| OpenCode | 插件化工具系统，高度可扩展 | 高度可扩展的第三方工具集成 |

---

## 8. 边界情况与错误处理

### 8.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| Handler 未找到 | tool_name 不在 registry 中 | `codex-rs/core/src/tools/registry.rs:201` |
| Payload 类型不匹配 | handler.kind() 与 payload 不匹配 | `codex-rs/core/src/tools/registry.rs:207` |
| Hook 中止 | after_tool_use 返回 FailedAbort | `codex-rs/core/src/tools/registry.rs:545` |
| 执行错误 | handler.handle() 返回 Err | `codex-rs/core/src/tools/registry.rs:220` |
| **Agent Job 超时** | **超过 max_runtime_seconds** | **`codex-rs/core/src/tools/handlers/agent_jobs.rs:40`** |
| **Agent Job 取消** | **任务被取消或父 Agent 终止** | **`codex-rs/state/src/runtime/agent_jobs.rs:271`** |

### 8.2 超时/资源限制

```rust
// 变异操作门控（概念代码）
if is_mutating {
    // 等待其他变异操作完成
    invocation.turn.tool_call_gate.wait_ready().await;
}
// 执行变异操作
let result = handler.handle(invocation).await;
```

### 8.3 Agent Jobs 边界情况

| 边界情况 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 子 Agent 失败 | 重试机制，最多 3 次 | `codex-rs/core/src/tools/handlers/agent_jobs.rs` |
| 线程深度超限 | 拒绝创建，返回错误 | `codex-rs/core/src/tools/handlers/agent_jobs.rs:1` (exceeds_thread_spawn_depth_limit) |
| 任务取消 | 标记 Cancelled，停止调度 | `codex-rs/state/src/runtime/agent_jobs.rs:271` |
| 结果上报超时 | 30 分钟默认超时 | `codex-rs/core/src/tools/handlers/agent_jobs.rs:40` |

### 8.4 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| `RespondToModel` | 将错误信息返回给 LLM，继续对话 | `codex-rs/core/src/tools/router.rs:411` |
| `Fatal` | 向上传播，终止当前 Turn | `codex-rs/core/src/tools/router.rs:410` |
| `MissingLocalShellCallId` | 返回格式错误给模型 | `codex-rs/core/src/tools/router.rs:344` |
| `PayloadMismatch` | 记录内部错误，返回 Fatal | `codex-rs/core/src/tools/registry.rs:209` |

---

## 9. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `codex-rs/core/src/tools/router.rs` | 372 | ToolRouter::dispatch_tool_call |
| 核心 | `codex-rs/core/src/tools/registry.rs` | 194 | ToolRegistry::dispatch |
| Handler Trait | `codex-rs/core/src/tools/registry.rs` | 163 | ToolHandler trait 定义 |
| 配置 | `codex-rs/core/src/tools/spec.rs` | 97 | ToolsConfig 结构定义 |
| 上下文 | `codex-rs/core/src/tools/context.rs` | 424 | ToolInvocation 结构 |
| MCP Handler | `codex-rs/core/src/tools/handlers/mcp.rs` | 256 | McpHandler::handle |
| Shell Handler | `codex-rs/core/src/tools/handlers/shell.rs` | - | ShellHandler 实现 |
| **Agent Jobs Handler** | **`codex-rs/core/src/tools/handlers/agent_jobs.rs`** | **34** | **BatchJobHandler 实现** |
| **Agent Jobs 状态** | **`codex-rs/state/src/model/agent_job.rs`** | **1** | **数据模型定义** |
| **Agent Jobs 存储** | **`codex-rs/state/src/runtime/agent_jobs.rs`** | **1** | **SQLite 操作** |
| **进度条 UI** | **`codex-rs/exec/src/event_processor_with_human_output.rs`** | **968** | **render_agent_job_progress** |

---

## 10. 延伸阅读

- 前置知识：`04-codex-agent-loop.md`
- 相关机制：`06-codex-mcp-integration.md`
- 深度分析：`docs/codex/questions/codex-tool-security.md`
- **新增功能**：`docs/codex/questions/codex-agent-jobs.md` (Agent Jobs 批量处理)

---

*✅ Verified: 基于 codex/codex-rs/core/src/tools/ 源码分析*
*基于版本：2026-02-08 | 最后更新：2026-03-03*
