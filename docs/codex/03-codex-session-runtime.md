# Session Runtime（codex）

本文基于 `codex/codex-rs/core/src` 源码，解释 Codex 的 Session Runtime——会话生命周期管理、状态存储和 Turn 调度的核心机制。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────┐
│  Session：会话生命周期管理                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ codex/codex-rs/core/src/codex.rs       │                     │
│  │  ├── struct Session                    │                     │
│  │  ├── fn new() -> Session               │                     │
│  │  ├── fn spawn_task()                   │                     │
│  │  └── fn abort_all_tasks()              │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  SessionState：持久化状态                                         │
│  ┌────────────────────────────────────────┐                     │
│  │ codex/codex-rs/core/src/state/session.rs│                    │
│  │  ├── history: ContextManager           │                     │
│  │  ├── session_configuration             │                     │
│  │  ├── mcp_connection_manager            │                     │
│  │  └── latest_rate_limits                │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  TurnContext：单次回合上下文                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ codex/codex-rs/core/src/codex.rs       │                     │
│  │  ├── model_info                        │                     │
│  │  ├── sandbox_policy                    │                     │
│  │  ├── approval_policy                   │                     │
│  │  └── tool_call_gate                    │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念

### 2.1 一句话定义

Codex 的 Session Runtime 采用「**单 Session 多 Turn，单 Turn 顺序执行**」的并发模型：一个会话可同时存在多个 Turn，但只有一个 Turn 处于活跃状态；每个 Turn 内部是顺序执行的 Agent Loop。

### 2.2 三层结构

| 层级 | 职责 | 生命周期 |
|------|------|----------|
| **Session** | 管理会话生命周期、状态持久化、任务调度 | 从启动到退出 |
| **Turn** | 单次用户交互的完整执行单元 | 从用户输入到完成 |
| **Task** | Turn 的后台执行载体（tokio::task） | 与 Turn 相同 |

---

## 3. Session 结构详解

### 3.1 Session 主结构

```rust
// codex/codex-rs/core/src/codex.rs
pub struct Session {
    /// 会话唯一标识
    conversation_id: ThreadId,
    
    /// 共享状态（Arc<Mutex<>> 包装）
    state: Arc<Mutex<SessionState>>,
    
    /// 服务集合（MCP、模型客户端等）
    services: Arc<SessionServices>,
    
    /// 当前活跃任务管理
    task_tracker: Arc<TaskTracker>,
    
    /// 事件发送通道（到 TUI）
    event_sender: Sender<Event>,
}
```

### 3.2 SessionState 详情

```rust
// codex/codex-rs/core/src/state/session.rs
pub(crate) struct SessionState {
    /// 会话配置
    pub(crate) session_configuration: SessionConfiguration,
    
    /// 历史记录管理（ContextManager）
    pub(crate) history: ContextManager,
    
    /// 最新速率限制信息
    pub(crate) latest_rate_limits: Option<RateLimitSnapshot>,
    
    /// MCP 依赖已提示集合（避免重复提示）
    pub(crate) mcp_dependency_prompted: HashSet<String>,
    
    /// 初始上下文已注入标记
    pub(crate) initial_context_seeded: bool,
    
    /// 之前的模型（用于检测切换）
    pub(crate) previous_model: Option<String>,
}
```

**设计意图**：
- `history` 使用 `ContextManager` 管理对话历史，支持 Token 估算和压缩
- `mcp_dependency_prompted` 防止重复询问用户安装 MCP 依赖
- `initial_context_seeded` 确保系统提示只注入一次

### 3.3 SessionServices 服务集合

```rust
// codex/codex-rs/core/src/state/service.rs
pub struct SessionServices {
    /// MCP 连接管理器
    pub mcp_connection_manager: RwLock<McpConnectionManager>,
    
    /// 模型客户端
    pub model_client: Arc<ModelClient>,
    
    /// 工具审批缓存
    pub tool_approvals: Mutex<ApprovalStore>,
    
    /// OpenTelemetry 管理器
    pub otel_manager: Arc<OtelManager>,
    
    /// 分析事件客户端
    pub analytics: Arc<AnalyticsEventsClient>,
}
```

---

## 4. Turn 生命周期管理

### 4.1 Turn 创建流程

```rust
// codex/codex-rs/core/src/codex.rs
async fn new_turn_with_sub_id(
    &self,
    sub_id: String,
    items: Vec<ResponseInputItem>,
    cwd: PathBuf,
    model_override: Option<String>,
) -> Result<Arc<TurnContext>> {
    // 1. 获取模型信息
    let model_info = self.get_model_info(model_override).await?;
    
    // 2. 构建 TurnContext
    let turn_context = Arc::new(TurnContext {
        sub_id,
        model_info,
        sandbox_policy: self.get_sandbox_policy().await,
        approval_policy: self.get_approval_policy().await,
        tools_config: self.build_tools_config().await,
        cwd,
        // ... 其他字段
    });
    
    Ok(turn_context)
}
```

### 4.2 TurnContext 关键字段

```rust
// codex/codex-rs/core/src/codex.rs
pub struct TurnContext {
    /// Turn 唯一标识
    pub sub_id: String,
    
    /// 模型信息（capabilities, context window 等）
    pub model_info: ModelInfo,
    
    /// 沙箱策略
    pub sandbox_policy: SandboxPolicy,
    
    /// 审批策略
    pub approval_policy: AskForApproval,
    
    /// 工具配置
    pub tools_config: ToolsConfig,
    
    /// 当前工作目录
    pub cwd: PathBuf,
    
    /// 工具调用门控（ReadinessFlag）
    pub tool_call_gate: ReadinessFlag,
    
    /// 协作模式
    pub collaboration_mode: CollaborationMode,
}
```

**工程 Trade-off**：
- ✅ `TurnContext` 包含单次交互的所有上下文，避免全局状态污染
- ✅ `tool_call_gate` 用于控制 mutating 工具的执行时机
- ⚠️ 字段较多，但通过 `Arc<TurnContext>` 共享，避免克隆开销

---

## 5. 任务调度机制

### 5.1 任务类型

```rust
// codex/codex-rs/core/src/tasks/mod.rs
pub enum Task {
    /// 普通用户交互 Turn
    Regular(RegularTask),
    
    /// 上下文压缩任务
    Compact(CompactTask),
    
    /// 远程压缩任务
    RemoteCompact(RemoteCompactTask),
}
```

### 5.2 任务启动与取消

```rust
// codex/codex-rs/core/src/codex.rs
pub async fn spawn_task(
    &self,
    turn_context: Arc<TurnContext>,
    task: RegularTask,
) -> Result<()> {
    // 1. 取消所有现有任务
    self.abort_all_tasks(TurnAbortReason::Replaced).await;
    
    // 2. 创建新的任务句柄
    let task_handle = tokio::spawn(task.run(turn_context.clone()));
    
    // 3. 记录活跃任务
    self.task_tracker.set_active(turn_context.sub_id.clone(), task_handle);
    
    Ok(())
}
```

**关键设计**：
- 新任务启动时自动取消旧任务（`Replaced` 原因）
- 使用 `TaskTracker` 管理任务生命周期
- 支持优雅取消（通过 `CancellationToken`）

### 5.3 任务状态流转

```
┌─────────┐    spawn     ┌─────────┐   complete   ┌─────────┐
│ Pending │ ───────────▶ │ Running │ ───────────▶ │ Done    │
└─────────┘              └─────────┘              └─────────┘
                              │
                              │ abort
                              ▼
                         ┌─────────┐
                         │ Aborted │
                         └─────────┘
```

---

## 6. 状态持久化

### 6.1 RolloutRecorder 事件持久化

```rust
// codex/codex-rs/core/src/rollout/mod.rs
pub struct RolloutRecorder {
    /// 会话 ID
    conversation_id: ThreadId,
    
    /// 事件存储（JSON Lines 格式）
    storage: Box<dyn RolloutStorage>,
}

/// 持久化事件
pub async fn record_item(&mut self, item: RolloutItem) -> Result<()> {
    self.storage.append(item).await
}
```

### 6.2 持久化内容

| 数据类型 | 存储格式 | 用途 |
|----------|----------|------|
| 对话历史 | JSON Lines | 会话恢复、分叉 |
| Token 使用 | 结构化数据 | 限流控制 |
| 工具调用记录 | 事件流 | 审计、调试 |
| 压缩历史 | 摘要内容 | 长会话管理 |

### 6.3 恢复机制

```rust
/// 从 rollout 文件恢复会话
pub async fn resume_from_rollout(
    conversation_id: ThreadId,
    storage: Arc<dyn RolloutStorage>,
) -> Result<SessionState> {
    // 1. 读取所有历史事件
    let items = storage.read_all().await?;
    
    // 2. 重建 ContextManager
    let mut history = ContextManager::new();
    for item in items {
        history.record_items(&[item.into()], TruncationPolicy::default());
    }
    
    // 3. 重建 SessionState
    Ok(SessionState {
        history,
        // ... 其他字段
    })
}
```

---

## 7. 与 Agent Loop 的关系

```
┌─────────────────────────────────────────────────────────────┐
│                     Session Runtime                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Session    │──▶│  TurnContext│──▶│  Agent Loop         │  │
│  │             │  │             │  │  (run_turn)         │  │
│  │ - 状态管理   │  │ - 单次上下文 │  │  - 采样请求         │  │
│  │ - 任务调度   │  │ - 工具配置   │  │  - 工具调用         │  │
│  │ - 事件路由   │  │ - 沙箱策略   │  │  - 历史更新         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│         │                │                                      │
│         ▼                ▼                                      │
│  ┌──────────────────────────────────┐                        │
│  │     SessionState (持久化)         │                        │
│  │  - history: ContextManager        │                        │
│  │  - configuration                  │                        │
│  └──────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 证据索引

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| Session | `core/src/codex.rs` | 会话主结构，任务调度 |
| SessionState | `core/src/state/session.rs` | 持久化状态 |
| SessionServices | `core/src/state/service.rs` | 服务集合 |
| TurnContext | `core/src/codex.rs` | 回合上下文 |
| Task | `core/src/tasks/mod.rs` | 任务定义 |
| RolloutRecorder | `core/src/rollout/mod.rs` | 事件持久化 |
| ContextManager | `core/src/context_manager/history.rs` | 历史管理 |

---

## 9. 架构特点总结

- **单活跃 Turn**：同一 Session 同时只有一个活跃 Turn，新 Turn 自动替换旧 Turn
- **分层状态**：Session 级状态持久化 + Turn 级上下文隔离
- **事件驱动**：通过通道（Channel）与 TUI 层解耦
- **可恢复性**：完整的 rollout 机制支持会话恢复和分叉
