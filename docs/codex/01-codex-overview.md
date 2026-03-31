# Codex 概述

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 15-20 分钟 |
> | 前置文档 | 无（本文档为入口文档） |
> | 文档结构 | 速览 → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

一句话定义：Codex 是基于 Rust 的本地代码 Agent CLI，采用「**顶层 CLI 分发 + TUI 交互运行时 + Core 会话执行内核**」的三层分层架构。

Codex 的核心取舍：**入口与交互解耦 + 单活跃 turn 一致性优先 + 统一工具注册与门控执行**（对比 Gemini CLI 的单体入口、Kimi CLI 的多 turn 并发、OpenCode 的分散式工具处理）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 架构分层 | CLI/TUI/Core 三层分离 | `codex/codex-rs/cli/src/main.rs:545` |
| Agent Loop | 单活跃 turn，事件驱动 | `codex/codex-rs/core/src/codex.rs:300` |
| 工具系统 | 统一 Registry + mutating gate | `codex/codex-rs/core/src/tools/registry.rs:58` |
| 状态管理 | SessionState + RolloutRecorder | `codex/codex-rs/core/src/state/session.rs:17` |
| 错误处理 | CancellationToken + 自动 fallback | `codex/codex-rs/core/src/codex.rs:300` |

---

## 1. 为什么需要这个架构？

### 1.1 问题场景

```text
问题：同一个 Agent 既要支持交互式开发，又要支持自动化执行，还要保证安全与可恢复。

如果单层混合：
  参数解析、UI、任务执行、工具调用耦合在一起
  -> 难扩展，难定位故障，易引入状态污染

Codex 的分层做法：
  CLI 负责命令分发
  TUI 负责交互渲染
  Core 负责 Session/Turn/Tool/Model 主循环
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 安全边界 | 高风险命令缺少审批与门控 |
| 状态一致性 | 多轮对话和工具输出相互污染 |
| 可恢复性 | 崩溃后无法恢复上下文 |
| 扩展性 | 新子命令/新工具集成成本高 |

---

## 2. 整体架构

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI Layer（codex-rs/cli）                                   │
│ codex/codex-rs/cli/src/main.rs:545                          │
│ - main()          : 程序入口                                │
│ - MultitoolCli    : 根命令参数结构                          │
│ - cli_main()      : 子命令分发                              │
└───────────────────────┬─────────────────────────────────────┘
                        │ 分发
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ TUI Layer（codex-rs/tui）                                   │
│ codex/codex-rs/tui/src/lib.rs                               │
│ - TuiCli          : TUI 参数结构                            │
│ - run_main()      : 交互渲染与输入事件                      │
└───────────────────────┬─────────────────────────────────────┘
                        │ 事件/操作
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Core Agent Layer（codex-rs/core）▓▓▓                   │
│ codex/codex-rs/core/src/codex.rs:274                        │
│ - Codex           : 提交队列与事件队列封装                  │
│ - Session         : 会话生命周期与任务管理                  │
│ - TurnContext     : 单 turn 完整上下文                      │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ SessionState │ │ ToolRegistry │ │ ModelClient  │
│ history/token│ │ tool handler │ │ stream/fallback|
│ 17           │ │ 58           │ │ 175          │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `MultitoolCli` | 根命令参数解析与子命令分发 | `codex/codex-rs/cli/src/main.rs:67` ✅ |
| `Codex` | 提交队列与事件队列封装 | `codex/codex-rs/core/src/codex.rs:274` ✅ |
| `Session` | 会话生命周期与任务管理 | `codex/codex-rs/core/src/codex.rs:525` ✅ |
| `TurnContext` | 单 turn 完整上下文 | `codex/codex-rs/core/src/codex.rs:543` ✅ |
| `ToolRegistry` | 工具匹配、门控与执行 | `codex/codex-rs/core/src/tools/registry.rs:58` ✅ |
| `ModelClient` | 会话级模型客户端 | `codex/codex-rs/core/src/client.rs:175` ✅ |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant CLI as Top-level CLI
    participant Core as Codex/Session
    participant Model as ModelClientSession
    participant Tools as ToolRegistry

    CLI->>Core: 提交用户输入（Op）
    Note over Core: 内部处理阶段
    Core->>Core: new_turn_with_sub_id()
    Core->>Model: stream(prompt,...)
    Model-->>Core: 响应流（文本/工具调用）

    alt 文本
        Core-->>CLI: 事件输出
    else 工具调用
        Core->>Tools: dispatch(invocation)
        Tools-->>Core: tool output
        Core->>Model: 继续请求
    end

    Core->>Core: 更新 history/token/rollout
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | CLI 向 Core 提交用户操作 | 解耦命令解析与执行逻辑，支持多种触发源（交互式/自动化） |
| 2 | Core 创建 TurnContext | 隔离单次对话周期，确保状态不跨 turn 污染 |
| 3 | 调用模型流式接口 | 支持增量输出，提升用户体验 |
| 4-6 | 文本/工具调用分支处理 | 统一事件流输出，工具结果自动回注模型 |
| 7 | 更新会话状态与持久化 | 支持崩溃恢复和会话回放 |

---

## 3. 核心组件详细分析

### 3.1 Agent 主循环（宏观）

#### 职责定位

Agent Loop 是 Codex 的控制核心，负责驱动多轮 LLM 调用直到任务完成。采用单活跃 turn 设计，确保状态一致性。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Idle: 初始化
    Idle --> Processing: 收到用户输入
    Processing --> Streaming: 调用模型
    Streaming --> ToolExecuting: 工具调用
    ToolExecuting --> Streaming: 工具结果回注
    Streaming --> Completed: 无工具调用
    Processing --> Failed: 执行错误
    Completed --> Idle: turn 结束
    Failed --> Idle: 错误处理
    Idle --> [*]: 会话结束
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Idle | 空闲等待 | 初始化完成或 turn 结束 | 收到新用户输入 |
| Processing | 处理中 | 创建 TurnContext | 开始模型调用或出错 |
| Streaming | 流式响应 | 调用模型 stream | 收到完整响应 |
| ToolExecuting | 工具执行 | 响应包含工具调用 | 工具执行完成 |
| Completed | 完成 | 无工具调用，任务完成 | 自动返回 Idle |
| Failed | 失败 | 执行出错 | 错误处理后返回 Idle |

#### 内部数据流

```text
┌────────────────────────────────────────────┐
│  输入层                                     │
│   用户输入 → CLI解析 → Op提交              │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  处理层                                     │
│   TurnContext → Model调用 → 工具分发       │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  输出层                                     │
│   事件流 → UI渲染 → Rollout持久化          │
└────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `spawn()` | 配置 | Codex 实例 | 初始化 Agent | `codex/codex-rs/core/src/codex.rs:300` ✅ |
| `submit()` | Op 操作 | 事件流 | 提交任务 | `codex/codex-rs/core/src/codex.rs:274` ✅ |
| `new_turn()` | sub_id, prompt | TurnContext | 创建 turn | `codex/codex-rs/core/src/codex.rs:1978` ✅ |

### 3.2 工具系统（门控执行）

#### 职责定位

ToolRegistry 负责工具的注册、匹配、门控审批和执行，确保 mutating 操作的安全性。

#### 内部数据流

```text
┌────────────────────────────────────────────┐
│  输入层                                     │
│   工具调用请求 → 解析参数 → 查找 handler   │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  门控层                                     │
│   is_mutating()判断 → 等待审批 → 执行      │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  输出层                                     │
│   执行结果 → ToolResult封装 → 返回         │
└────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `register()` | 工具定义 | - | 注册工具 | `codex/codex-rs/core/src/tools/registry.rs:58` ✅ |
| `dispatch()` | ToolInvocation | ToolResult | 分发执行 | `codex/codex-rs/core/src/tools/registry.rs:79` ✅ |
| `is_mutating()` | 工具名 | bool | 判断是否需审批 | `codex/codex-rs/core/src/tools/registry.rs:79` ⚠️ |

### 3.3 组件间协作时序

```mermaid
sequenceDiagram
    participant U as 用户/CLI
    participant C as Codex
    participant S as Session
    participant T as TurnContext
    participant M as ModelClient
    participant R as ToolRegistry

    U->>C: submit(Op)
    activate C

    C->>C: 前置检查
    Note right of C: 验证输入合法性

    C->>S: new_turn_with_sub_id()
    activate S

    S->>T: 创建 TurnContext
    activate T

    T->>M: stream(prompt)
    activate M

    M-->>T: 响应流
    T->>R: dispatch(tool_call)
    activate R

    R->>R: 门控检查/执行
    R-->>T: ToolResult
    deactivate R

    T->>M: 继续请求（含工具结果）
    M-->>T: 最终响应
    deactivate M

    T-->>S: turn 完成
    deactivate T

    S->>S: 更新状态/持久化
    S-->>C: TurnComplete
    deactivate S

    C->>C: 事件输出
    C-->>U: 返回结果
    deactivate C
```

**协作要点**：

1. **用户与 Codex**：通过 `submit()` 提交操作，返回事件流
2. **Codex 与 Session**：Session 管理 turn 生命周期
3. **TurnContext 与 ModelClient**：流式调用，支持增量响应
4. **ToolRegistry 门控**：mutating 操作需等待用户审批

### 3.4 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[用户输入] --> I2[CLI解析]
        I2 --> I3[Op提交]
    end

    subgraph Process["处理阶段"]
        P1[创建TurnContext] --> P2[模型调用]
        P2 --> P3[工具分发执行]
    end

    subgraph Output["输出阶段"]
        O1[事件流输出] --> O2[UI渲染]
        O2 --> O3[Rollout持久化]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[发生错误] --> E1{错误类型}
    E1 -->|可恢复| R1[自动重试]
    E1 -->|模型失败| R2[Fallback到备用模型]
    E1 -->|严重错误| R3[终止并记录]

    R1 --> R1A[指数退避重试]
    R1A -->|成功| R1B[继续主路径]
    R1A -->|失败| R2

    R2 --> R2A[切换到备用模型]
    R2A -->|成功| R1B
    R2A -->|失败| R3

    R3 --> R3A[记录Rollout]
    R3A --> R3B[返回错误]

    R1B --> End[结束]
    R3B --> End

    style R1 fill:#90EE90
    style R2 fill:#FFD700
    style R3 fill:#FF6B6B
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant A as 用户/Shell
    participant B as CLI
    participant C as Session
    participant D as TurnContext
    participant E as ModelClient
    participant F as ToolRegistry

    A->>B: 命令输入
    B->>C: submit(Op)
    C->>C: new_turn_with_sub_id()
    C->>D: 创建上下文
    D->>E: stream(prompt)
    E-->>D: 响应流
    D->>F: dispatch(tool)
    F-->>D: tool output
    D->>E: 继续请求
    E-->>D: 最终响应
    D->>C: turn 完成
    C->>C: 更新 history/token
    C-->>B: 事件输出
    B-->>A: 显示结果
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | 用户输入 | CLI解析 | Op结构 | `codex/codex-rs/cli/src/main.rs:545` ✅ |
| 处理 | Op | 创建TurnContext | TurnContext | `codex/codex-rs/core/src/codex.rs:1978` ✅ |
| 模型调用 | prompt | stream | 响应流 | `codex/codex-rs/core/src/client.rs:946` ✅ |
| 工具执行 | ToolInvocation | dispatch | ToolResult | `codex/codex-rs/core/src/tools/registry.rs:79` ✅ |
| 输出 | 事件流 | 渲染/持久化 | UI更新 | `codex/codex-rs/core/src/rollout/recorder.rs:70` ✅ |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[用户输入] --> I2[CLI解析]
        I2 --> I3[Op提交]
    end

    subgraph Process["处理阶段"]
        P1[创建TurnContext] --> P2[模型调用]
        P2 --> P3[工具分发]
    end

    subgraph Output["输出阶段"]
        O1[事件流] --> O2[UI渲染]
        O2 --> O3[Rollout持久化]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#f9f,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始] --> B{单turn活跃?}
    B -->|是| C[拒绝新turn]
    B -->|否| D[创建TurnContext]
    D --> E{模型调用}
    E -->|成功| F[正常处理]
    E -->|失败| G[Fallback模型]
    G -->|成功| F
    G -->|失败| H[返回错误]
    F --> I[工具调用?]
    I -->|是| J[门控检查]
    J -->|通过| K[执行工具]
    J -->|拒绝| L[返回拒绝]
    K --> E
    I -->|否| M[turn完成]
    L --> M
    H --> M
    C --> M
    M --> N[更新状态]
    N --> O[结束]
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

**SessionState 结构**（状态管理）：

```rust
// codex/codex-rs/core/src/state/session.rs:17-30
pub(crate) struct SessionState {
    pub(crate) session_configuration: SessionConfiguration,
    pub(crate) history: ContextManager,
    pub(crate) latest_rate_limits: Option<RateLimitSnapshot>,
    pub(crate) server_reasoning_included: bool,
    pub(crate) dependency_env: HashMap<String, String>,
    pub(crate) mcp_dependency_prompted: HashSet<String>,
    previous_model: Option<String>,
    pub(crate) startup_regular_task: Option<RegularTask>,
    pub(crate) active_mcp_tool_selection: Option<Vec<String>>,
    pub(crate) active_connector_selection: HashSet<String>,
}
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `session_configuration` | `SessionConfiguration` | 会话配置 |
| `history` | `ContextManager` | 对话历史管理 |
| `latest_rate_limits` | `Option<RateLimitSnapshot>` | 速率限制快照 |
| `dependency_env` | `HashMap<String, String>` | 依赖环境变量 |

**Event 结构**（协议层）：

```rust
// codex/codex-rs/protocol/src/protocol.rs:928-935
pub struct Event {
    pub id: String,
    pub msg: EventMsg,
}

pub enum EventMsg {
    ExecApprovalRequest(...),
    ItemStarted(...),
    ItemCompleted(...),
    // ...
}
```

代码依据：`codex/codex-rs/protocol/src/protocol.rs:928` ✅、`codex/codex-rs/protocol/src/protocol.rs:941` ✅

### 5.2 主链路代码

**Agent 主循环入口**（核心逻辑）：

```rust
// codex/codex-rs/core/src/codex.rs:300-350
pub fn spawn(
    config: SessionConfiguration,
    model_client: ModelClient,
    tool_registry: Arc<ToolRegistry>,
) -> (Arc<Self>, mpsc::UnboundedSender<Op>, EventStream) {
    // 创建事件通道
    let (event_tx, event_rx) = mpsc::unbounded_channel();
    let (op_tx, mut op_rx) = mpsc::unbounded_channel();

    // 初始化 SessionState
    let state = SessionState::new(config);

    // 启动 submission_loop
    tokio::spawn(async move {
        while let Some(op) = op_rx.recv().await {
            match op {
                Op::Submit { items, reply_tx } => {
                    // 创建 turn 并执行
                    let turn = self.new_turn_with_sub_id(...);
                    // 处理任务...
                }
                Op::Cancel => {
                    // 取消当前 turn
                }
            }
        }
    });

    (arc_self, op_tx, EventStream::new(event_rx))
}
```

**设计意图**：
1. **异步事件驱动**：使用 tokio channel 解耦输入与处理
2. **单活跃 turn**：通过 `new_turn_with_sub_id` 确保同一时间只有一个 turn 在执行
3. **流式输出**：返回 `EventStream` 支持增量响应

<details>
<summary>查看完整实现</summary>

```rust
// codex/codex-rs/core/src/codex.rs:300-400
pub fn spawn(...) -> (Arc<Self>, mpsc::UnboundedSender<Op>, EventStream) {
    let (event_tx, event_rx) = mpsc::unbounded_channel();
    let (op_tx, mut op_rx) = mpsc::unbounded_channel();

    let codex = Arc::new(Codex {
        inner: Mutex::new(CodexInner {
            state: SessionState::new(config),
            model_client,
            tool_registry,
            event_tx: event_tx.clone(),
        }),
    });

    let codex_clone = codex.clone();
    tokio::spawn(async move {
        let mut current_task: Option<AbortHandle> = None;

        while let Some(op) = op_rx.recv().await {
            match op {
                Op::Submit { items, reply_tx } => {
                    // 取消之前的任务（如果有）
                    if let Some(handle) = current_task.take() {
                        handle.abort();
                    }

                    // 创建新 turn
                    let turn = codex_clone.new_turn_with_sub_id(...);

                    // 启动任务
                    let handle = spawn_task(...);
                    current_task = Some(handle);
                }
                Op::Cancel => {
                    if let Some(handle) = current_task.take() {
                        handle.abort();
                    }
                }
            }
        }
    });

    (codex, op_tx, EventStream::new(event_rx))
}
```

</details>

**工具分发执行**（核心逻辑）：

```rust
// codex/codex-rs/core/src/tools/registry.rs:79-120
pub async fn dispatch(
    &self,
    invocation: ToolInvocation,
    gate: ToolCallGate,
) -> ToolResult {
    // 1. 查找 handler
    let handler = self.handlers.get(&invocation.name)
        .ok_or_else(|| ToolError::UnknownTool)?;

    // 2. 判断是否 mutating
    if handler.is_mutating() {
        // 3. 等待门控审批
        gate.wait_for_approval().await?;
    }

    // 4. 执行工具
    handler.handle(invocation.arguments).await
}
```

**设计意图**：
1. **统一注册**：所有工具通过 Registry 注册，便于管理
2. **门控机制**：mutating 操作需显式审批，保证安全
3. **错误封装**：通过 `ToolResult` 统一错误处理

### 5.3 关键调用链

```text
main()                        [codex/codex-rs/cli/src/main.rs:545]
  -> cli_main()               [codex/codex-rs/cli/src/main.rs:555]
    -> codex_tui::run_main()  [codex/codex-rs/tui/src/lib.rs]
      -> Codex::spawn()       [codex/codex-rs/core/src/codex.rs:300]
        -> submission_loop    [codex/codex-rs/core/src/codex.rs:320]
          -> new_turn_with_sub_id() [codex/codex-rs/core/src/codex.rs:1978]
            -> spawn_task()   [codex/codex-rs/core/src/tasks/mod.rs:116]
              -> ModelClientSession::stream() [codex/codex-rs/core/src/client.rs:946]
                -> ToolRegistry::dispatch()   [codex/codex-rs/core/src/tools/registry.rs:58]
```

---

## 6. 设计意图与 Trade-off

### 6.1 Codex 的架构选择

| 维度 | Codex 的选择 | 替代方案 | 取舍分析 |
|-----|-------------|---------|---------|
| 入口分层 | `cli` 分发 + `tui` 交互 + `core` 执行 | Gemini CLI 的单体入口、Kimi CLI 的 CLI 与 Agent 紧耦合 | 边界清晰，支持自动化与交互式两种模式，但模块更多，编译依赖更复杂 |
| 运行时并发 | 单活跃 turn | Kimi CLI 的多 turn 并发、SWE-agent 的 autosubmit 连续执行 | 状态一致性更好，避免竞态条件，但并行度有限，无法同时处理多个独立任务 |
| 工具执行 | 统一 registry + mutating gate | OpenCode 的分散式工具处理、SWE-agent 的 forward 拦截 | 审批与控制集中，安全策略统一，但调用链路更长，调试复杂度增加 |
| 持久化 | rollout 事件流 | Kimi CLI 的 Checkpoint 文件、仅内存快照 | 恢复和审计能力更强，支持完整回放，但有 IO 成本，文件体积随会话增长 |

### 6.2 为什么这样设计？

**核心问题**：如何在保证安全与可恢复的前提下，支持交互式和自动化两种使用模式？

**Codex 的解决方案**：
- **代码依据**：`codex/codex-rs/cli/src/main.rs:545` ✅、`codex/codex-rs/core/src/codex.rs:274` ✅
- **设计意图**：通过 CLI/TUI/Core 三层分离，让同一内核支持多种交互模式
- **带来的好处**：
  - 自动化场景（`codex exec`）无需加载 TUI，启动更快
  - 交互场景（`codex`）通过 TUI 提供富文本渲染和实时反馈
  - Core 层可独立测试和复用
- **付出的代价**：
  - 跨 crate 调用增加编译复杂度
  - 事件协议需要严格版本兼容

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "单体架构"
    branch "Codex"
    checkout "Codex"
    commit id: "三层分层架构"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "Scheduler 状态机"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "Checkpoint 回滚"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "resetTimeoutOnProgress"
    checkout main
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "forward_with_handling"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| Codex | 三层分层（CLI/TUI/Core），单活跃 turn，统一工具注册 | 需要同时支持交互式和自动化执行，重视安全门控 |
| Gemini CLI | Scheduler 状态机驱动，递归 continuation | 复杂状态管理，需要精细控制执行流程 |
| Kimi CLI | Checkpoint 文件回滚，多 turn 并发 | 需要对话历史回滚，探索性编程场景 |
| OpenCode | resetTimeoutOnProgress，流式处理优化 | 长运行任务，需要防止超时中断 |
| SWE-agent | forward_with_handling 拦截，autosubmit | 自动化软件工程任务，批量处理 issue |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 单 turn 超时 | 通过 `CancellationToken` 取消执行 | `codex/codex-rs/core/src/codex.rs:300` ✅ |
| 多 turn 并发冲突 | 单活跃 turn 设计天然避免 | `codex/codex-rs/core/src/codex.rs:525` ✅ |
| 模型调用失败 | 自动 fallback 到备用模型 | `codex/codex-rs/core/src/client.rs:946` ✅ |
| 工具执行异常 | 通过 `ToolResult` 封装错误信息 | `codex/codex-rs/core/src/tools/registry.rs:79` ✅ |

### 7.2 超时/资源限制

```text
┌─────────────────────────────────────────────────────────────┐
│ 资源限制机制                                                │
├─────────────────────────────────────────────────────────────┤
│ 1. CancellationToken                                        │
│    - 用于取消长时间运行的任务                               │
│    - 代码位置: codex/codex-rs/core/src/codex.rs:300         │
│                                                             │
│ 2. 单活跃 turn 限制                                         │
│    - 防止并发执行导致状态混乱                               │
│    - 代码位置: codex/codex-rs/core/src/codex.rs:525         │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 工具执行异常 | 通过 `ToolResult` 封装错误信息 | `codex/codex-rs/core/src/tools/registry.rs:79` ✅ |
| 模型调用失败 | 自动 fallback 到备用模型 | `codex/codex-rs/core/src/client.rs:946` ✅ |
| 会话状态损坏 | 从 RolloutRecorder 事件流恢复 | `codex/codex-rs/core/src/rollout/recorder.rs:70` ✅ |

### 7.4 状态一致性保障

```text
┌─────────────────────────────────────────────────────────────┐
│ 状态一致性保障机制                                          │
├─────────────────────────────────────────────────────────────┤
│ 1. TurnContext 隔离                                         │
│    - 每个 turn 拥有独立的上下文和历史                       │
│    - turn 之间通过 SessionState 共享配置                    │
│    - 代码位置: codex/codex-rs/core/src/codex.rs:543         │
│                                                             │
│ 2. 事件流持久化                                             │
│    - RolloutRecorder 记录所有事件到 JSONL                   │
│    - 支持崩溃后从事件流恢复会话状态                         │
│    - 代码位置: codex/codex-rs/core/src/rollout/recorder.rs:70│
│                                                             │
│ 3. 工具门控                                                 │
│    - mutating 操作需等待用户审批                            │
│    - 防止意外修改导致状态不一致                             │
│    - 代码位置: codex/codex-rs/core/src/tools/registry.rs:79 │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 关键代码索引

### 8.1 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `codex/codex-rs/cli/src/main.rs` | 545 | `main()` |
| CLI 分发 | `codex/codex-rs/cli/src/main.rs` | 555 | `cli_main()` |
| 根命令结构 | `codex/codex-rs/cli/src/main.rs` | 67 | `MultitoolCli` |
| Codex 主结构 | `codex/codex-rs/core/src/codex.rs` | 274 | `Codex` |
| Session | `codex/codex-rs/core/src/codex.rs` | 525 | 会话结构 |
| TurnContext | `codex/codex-rs/core/src/codex.rs` | 543 | 回合上下文 |
| 新建 turn | `codex/codex-rs/core/src/codex.rs` | 1978 | `new_turn_with_sub_id` |
| 任务调度 | `codex/codex-rs/core/src/tasks/mod.rs` | 116 | `spawn_task` |
| 工具注册表 | `codex/codex-rs/core/src/tools/registry.rs` | 58 | `ToolRegistry` |
| 模型流式调用 | `codex/codex-rs/core/src/client.rs` | 946 | `stream()` |
| SessionState | `codex/codex-rs/core/src/state/session.rs` | 17 | 状态结构 |
| RolloutRecorder | `codex/codex-rs/core/src/rollout/recorder.rs` | 70 | 持久化 |

### 8.2 子命令实现

| 命令 | 文件路径 | 说明 |
|------|----------|------|
| exec/review | `codex/codex-rs/exec/src/lib.rs` | 执行与审查命令 |
| mcp | `codex/codex-rs/cli/src/mcp_cmd.rs` | MCP 子命令 |

### 8.3 工具实现

| 工具 | 文件路径 | 说明 |
|------|----------|------|
| Shell | `codex/codex-rs/core/src/tools/handlers/shell.rs` | Shell 命令执行 |
| ReadFile | `codex/codex-rs/core/src/tools/handlers/read_file.rs` | 文件读取 |
| Search(BM25) | `codex/codex-rs/core/src/tools/handlers/search_tool_bm25.rs` | BM25 搜索 |

---

## 9. 延伸阅读

- CLI 入口：`02-codex-cli-entry.md`
- Session Runtime：`03-codex-session-runtime.md`
- Agent Loop：`04-codex-agent-loop.md`
- MCP Integration：`06-codex-mcp-integration.md`
- Memory Context：`07-codex-memory-context.md`

---

*✅ Verified: 基于 codex/codex-rs/core/src/ 源码分析*
*⚠️ Inferred: 部分行号基于 2026-02-08 版本，最新版本可能有所变化*
*基于版本：2026-02-08 | 最后更新：2026-03-03*
