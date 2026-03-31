# Gemini CLI Tool Call 并发机制

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 15-20 分钟 |
> | 前置文档 | `docs/gemini-cli/04-gemini-cli-agent-loop.md`、`docs/gemini-cli/05-gemini-cli-tools-system.md` |
> | 文档结构 | 结论先行 → 架构位置 → 核心组件 → 数据流转 → 关键代码 → 设计对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

**一句话定义**：Gemini CLI 采用"单 active call + 队列串行执行"的调度策略，通过 Scheduler 状态机管理工具调用生命周期，确保同一时间只有一个工具在执行。

Gemini CLI 的核心取舍：**串行执行 + 状态机管理**（对比 Kimi CLI 的并发派发、顺序收集策略）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 执行模式 | 单 active call 串行执行 | `packages/core/src/scheduler/scheduler.ts:388` |
| 状态管理 | 7 状态显式状态机（Validating/Scheduled/Executing 等） | `packages/core/src/scheduler/types.ts:25` |
| 队列策略 | 双层队列缓冲（requestQueue + state.queue） | `packages/core/src/scheduler/scheduler.ts:169` |
| 并发控制 | `isActive` 标志位检查 | `packages/core/src/scheduler/scheduler.ts:439` |

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

在 AI Coding Agent 中，LLM 一次可能请求执行多个工具（如同时读取多个文件、执行多个命令）。如何处理这些工具调用直接影响：

- **执行顺序**：工具间是否存在依赖关系？
- **资源竞争**：多个工具同时操作同一资源会怎样？
- **结果确定性**：并行执行是否会导致非确定性结果？

```
示例场景：
LLM 请求：1) 读取 package.json  2) 读取 src/index.ts  3) 执行 npm test

串行策略：按顺序执行，每个完成后才执行下一个
并行策略：同时发起多个调用，结果按完成顺序返回
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 工具依赖 | 并行执行可能导致依赖工具尚未完成就被使用 |
| 资源冲突 | 多个写操作同时执行可能导致数据不一致 |
| 结果排序 | 并行结果需要额外机制保证按请求顺序返回 |
| 错误隔离 | 一个工具失败不应影响其他工具的执行 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Runtime                                 │
│ packages/core/src/core/chat-service.ts                       │
└───────────────────────┬─────────────────────────────────────┘
                        │ 调用 schedule(requests)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Tool Scheduler ▓▓▓                                      │
│ packages/core/src/scheduler/scheduler.ts                     │
│ - schedule()       : 接收工具请求入口                       │
│ - _startBatch()    : 批量启动工具调用                       │
│ - _processQueue()  : 核心调度循环                           │
│ - _processNextItem(): 单步执行                              │
└───────────────────────┬─────────────────────────────────────┘
                        │ 串行执行
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Tool Execution│ │ Policy/Approval│ │ State Store  │
│ 工具实际执行  │ │ 权限审批       │ │ 状态管理     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `Scheduler` | 核心调度器，管理工具调用队列和状态流转 | `packages/core/src/scheduler/scheduler.ts:90` |
| `CoreToolScheduler` | 兼容层调度器，同样实现单活跃调用语义 | `packages/core/src/core/coreToolScheduler.ts:98` |
| `SchedulerStateManager` | 管理工具调用状态和队列 | `packages/core/src/scheduler/state-manager.ts` |
| `ToolCallRequestInfo` | 工具请求数据结构定义 | `packages/core/src/scheduler/types.ts:35` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant B as Scheduler
    participant C as Tool Execution
    participant D as State Manager

    A->>B: 1. schedule(requests)
    Note over B: 检查 isProcessing / isActive
    B->>B: 2. 若忙则入 requestQueue
    B->>B: 3. _startBatch() 转换请求
    B->>D: 4. state.enqueue(newCalls)
    B->>B: 5. _processQueue() 循环
    B->>B: 6. _processNextItem() 单步
    B->>C: 7. 执行 active calls
    C-->>B: 8. 返回结果
    B->>D: 9. finalize 状态更新
    B-->>A: 10. 返回结果数组
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent Loop 批量提交工具请求 | 支持一次多工具请求，但内部串行处理 |
| 2-3 | 队列缓冲与批量启动 | 解耦请求接收与执行，支持异步调度 |
| 5-6 | 串行处理循环 | 确保同一时间只有一个 active call |
| 7-8 | 同步执行工具 | 等待工具完成才继续下一个 |
| 9-10 | 状态更新与返回 | 每个工具完成后立即更新状态并返回结果 |

---

## 3. 核心组件详细分析

### 3.1 Scheduler 内部结构

#### 职责定位

Scheduler 是 Gemini CLI 工具调度的核心，负责将并发的工具请求转换为串行的执行流程，并通过状态机管理每个工具调用的生命周期。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Validating: 收到请求
    Validating --> Scheduled: 验证通过
    Validating --> Error: 验证失败
    Scheduled --> AwaitingApproval: 需要审批
    AwaitingApproval --> Executing: 审批通过
    AwaitingApproval --> Cancelled: 审批拒绝
    Scheduled --> Executing: 无需审批
    Executing --> Success: 执行成功
    Executing --> Error: 执行失败
    Executing --> Cancelled: 用户取消
    Success --> [*]
    Error --> [*]
    Cancelled --> [*]
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Validating | 验证请求合法性 | 收到新请求 | 验证完成 |
| Scheduled | 已排程等待执行 | 验证通过 | 开始执行或需要审批 |
| AwaitingApproval | 等待用户审批 | 策略要求审批 | 审批通过/拒绝 |
| Executing | 正在执行中 | 审批通过或无需审批 | 执行完成/失败/取消 |
| Success | 执行成功 | 工具返回结果 | 自动结束 |
| Error | 执行失败 | 工具抛出异常 | 自动结束 |
| Cancelled | 已取消 | 用户取消或审批拒绝 | 自动结束 |

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  输入层                                                      │
│  ├── ToolCallRequestInfo ──► 验证器 ──► ToolCall            │
│  └── 元数据(checkpoint等) ──► 解析器 ──► 状态对象            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  调度层                                                      │
│  ├── requestQueue: 等待处理的请求队列                        │
│  ├── state.queue: 已转换为 ToolCall 的内部队列               │
│  ├── allActiveCalls: 当前活跃调用列表                        │
│  └── _processQueue(): 循环处理直到队列为空                   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层                                                      │
│  ├── CompletedToolCall 结果封装                              │
│  ├── 回调通知 Agent Loop                                     │
│  └── 状态持久化                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `schedule()` | `ToolCallRequestInfo[]` | `CompletedToolCall[]` | 工具调度入口 | `packages/core/src/scheduler/scheduler.ts:169` |
| `_processQueue()` | `AbortSignal` | `void` | 核心调度循环 | `packages/core/src/scheduler/scheduler.ts:373` |
| `_processNextItem()` | `AbortSignal` | `boolean` | 单步执行 | `packages/core/src/scheduler/scheduler.ts:384` |

---

### 3.2 组件间协作时序

展示多个组件如何协作完成一个复杂操作。

```mermaid
sequenceDiagram
    participant U as Agent Loop
    participant A as Scheduler
    participant B as Policy/Approval
    participant C as Tool Executor
    participant S as State Manager

    U->>A: schedule([call1, call2])
    activate A

    A->>A: 前置检查
    Note right of A: 检查 isProcessing / isActive

    A->>A: _startBatch()
    activate A

    A->>S: state.enqueue([call1, call2])
    A->>A: _processQueue()
    A->>A: _processNextItem()
    A->>B: 检查审批策略
    B-->>A: 返回审批结果
    A->>C: 执行工具调用
    activate C

    C->>C: 实际工具执行
    C-->>A: 返回执行结果
    deactivate C

    A->>S: finalize 状态更新
    A->>A: 继续处理队列
    deactivate A

    A->>A: 结果组装
    A-->>U: 返回 CompletedToolCall[]
    deactivate A
```

**协作要点**：

1. **Agent Loop 与 Scheduler**：Agent Loop 批量提交请求，Scheduler 内部串行化处理
2. **Scheduler 与 Policy**：每个工具调用前检查审批策略，决定是否需用户确认
3. **Tool Executor 与 State Manager**：执行完成后立即更新状态，确保状态一致性

---

### 3.3 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[ToolCallRequestInfo] --> I2[验证转换]
        I2 --> I3[ToolCall 对象]
    end

    subgraph Process["调度阶段"]
        P1[入队 state.queue] --> P2[_processQueue 循环]
        P2 --> P3[_processNextItem 单步]
    end

    subgraph Output["输出阶段"]
        O1[执行工具] --> O2[状态流转]
        O2 --> O3[CompletedToolCall]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[发生错误] --> E1{错误类型}
    E1 -->|验证失败| R1[Validating -> Error]
    E1 -->|执行失败| R2[Executing -> Error]
    E1 -->|用户取消| R3[Executing -> Cancelled]
    E1 -->|审批拒绝| R4[AwaitingApproval -> Cancelled]

    R1 --> R1A[finalize 错误状态]
    R2 --> R2A[finalize 错误状态]
    R3 --> R3A[finalize 取消状态]
    R4 --> R4A[跳过执行]

    R1A --> End[继续处理队列]
    R2A --> End
    R3A --> End
    R4A --> End

    style R1 fill:#FFD700
    style R2 fill:#FFD700
    style R3 fill:#FF6B6B
    style R4 fill:#FF6B6B
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as Scheduler
    participant C as Policy/Approval
    participant D as Tool Execution
    participant S as State Manager

    A->>B: schedule([call1, call2])
    Note over B: 当前无活跃调用
    B->>B: _startBatch()
    B->>S: state.enqueue([call1, call2])
    B->>B: _processQueue(signal)

    B->>B: _processNextItem()
    B->>S: dequeue call1
    B->>B: Validating -> Scheduled
    B->>C: 检查审批策略
    C-->>B: 无需审批
    B->>B: Scheduled -> Executing
    B->>D: 执行 call1
    activate D
    D-->>B: 返回结果
    deactivate D
    B->>S: Executing -> Success
    B->>S: finalize call1

    B->>B: _processNextItem()
    B->>S: dequeue call2
    B->>D: 执行 call2
    activate D
    D-->>B: 返回结果
    deactivate D
    B->>S: finalize call2

    B->>B: 队列为空，结束循环
    B-->>A: 返回 CompletedToolCall[]
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | `ToolCallRequestInfo[]` | 验证并转换为 `ToolCall` | 内部状态对象 | `packages/core/src/scheduler/scheduler.ts:169-186` |
| 调度 | `ToolCall[]` | 入队并启动处理循环 | 排程状态 | `packages/core/src/scheduler/scheduler.ts:265-309` |
| 执行 | `ToolCall` | 状态流转 + 实际执行 | `CompletedToolCall` | `packages/core/src/scheduler/scheduler.ts:384-492` |
| 输出 | 执行结果 | 格式化 + 返回 | `CompletedToolCall[]` | `packages/core/src/scheduler/scheduler.ts:946-1024` |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[ToolCallRequestInfo]
        I1a[callId] --> I1
        I1b[name/args] --> I1
        I1c[prompt_id] --> I1
    end

    subgraph Process["处理阶段"]
        P1[ToolCall 包装] --> P2[状态机流转]
        P2 --> P3[串行执行]
    end

    subgraph Output["输出阶段"]
        O1[CompletedToolCall]
        O1a[SuccessfulToolCall] --> O1
        O1b[ErroredToolCall] --> O1
        O1c[CancelledToolCall] --> O1
    end

    I1 --> P1
    P3 --> O1

    style Process fill:#f9f,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[schedule 接收请求] --> B{isProcessing?}
    B -->|是| C[入 requestQueue]
    B -->|否| D[_startBatch]
    C --> E[等待当前批次完成]
    E --> D

    D --> F[_processQueue 循环]
    F --> G{signal.aborted?}
    G -->|是| H[取消所有队列]
    G -->|否| I[_processNextItem]

    I --> J{isActive?}
    J -->|是| K[等待当前完成]
    J -->|否| L[dequeue 下一个]

    L --> M[状态流转]
    M --> N[执行工具]
    N --> O{执行结果}
    O -->|成功| P[Success]
    O -->|失败| Q[Error]
    O -->|取消| R[Cancelled]

    P --> S[finalize]
    Q --> S
    R --> S
    S --> T{队列空?}
    T -->|否| F
    T -->|是| U[返回结果]

    style H fill:#FF6B6B
    style Q fill:#FFD700
    style R fill:#FF6B6B
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```typescript
// packages/core/src/scheduler/types.ts:35-45
export interface ToolCallRequestInfo {
  callId: string;              // 唯一调用标识
  name: string;                // 工具名称
  args: Record<string, unknown>; // 工具参数
  isClientInitiated: boolean;  // 是否客户端发起
  prompt_id: string;           // 关联的 prompt ID
  checkpoint?: string;         // 可选 checkpoint 引用
  traceId?: string;
  parentCallId?: string;
  schedulerId?: string;
}

// packages/core/src/scheduler/types.ts:25-33
export enum CoreToolCallStatus {
  Validating = 'validating',        // 验证中
  Scheduled = 'scheduled',          // 已排程
  Executing = 'executing',          // 执行中
  AwaitingApproval = 'awaiting_approval',  // 等待审批
  Success = 'success',              // 成功
  Error = 'error',                  // 错误
  Cancelled = 'cancelled',          // 已取消
}
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `callId` | `string` | 唯一标识每个工具调用，用于结果匹配 |
| `name` | `string` | 工具名称，决定执行哪个工具 |
| `args` | `Record<string, unknown>` | 工具参数，传递给工具执行器 |
| `prompt_id` | `string` | 关联的 prompt，用于上下文追踪 |
| `checkpoint` | `string?` | 可选的 checkpoint 引用，支持状态回滚 |

### 5.2 主链路代码

**关键代码**（核心逻辑）：

```typescript
// packages/core/src/scheduler/scheduler.ts:169-186
async schedule(
  request: ToolCallRequestInfo | ToolCallRequestInfo[],
  signal: AbortSignal,
): Promise<CompletedToolCall[]> {
  return runInDevTraceSpan(
    { name: 'schedule' },
    async ({ metadata: spanMetadata }) => {
      const requests = Array.isArray(request) ? request : [request];
      spanMetadata.input = requests;

      // ✅ Verified: 若当前有执行中请求，则入队等待
      if (this.isProcessing || this.state.isActive) {
        return this._enqueueRequest(requests, signal);
      }

      return this._startBatch(requests, signal);
    },
  );
}

// packages/core/src/scheduler/scheduler.ts:373-378
private async _processQueue(signal: AbortSignal): Promise<void> {
  while (this.state.queueLength > 0 || this.state.isActive) {
    const shouldContinue = await this._processNextItem(signal);
    if (!shouldContinue) break;
  }
}

// packages/core/src/scheduler/scheduler.ts:384-400
private async _processNextItem(signal: AbortSignal): Promise<boolean> {
  if (signal.aborted || this.isCancelling) {
    this.state.cancelAllQueued('Operation cancelled');
    return false;
  }

  // ✅ Verified: 检查 isActive 确保串行执行
  if (!this.state.isActive) {
    const next = this.state.dequeue();
    if (!next) return false;
    // ... 处理下一个调用
  }
  // ...
}
```

**设计意图**：
1. **串行执行保证**：`_processNextItem()` 中检查 `state.isActive`，确保同一时间只有一个活跃调用
2. **队列驱动**：`_processQueue()` 循环处理直到队列为空，天然支持批量请求的串行化
3. **状态分离**：`isProcessing`（调度器状态）与 `state.isActive`（工具状态）双层检查

<details>
<summary>查看完整实现</summary>

```typescript
// packages/core/src/scheduler/scheduler.ts:265-309
private async _startBatch(
  requests: ToolCallRequestInfo[],
  signal: AbortSignal,
): Promise<CompletedToolCall[]> {
  this.isProcessing = true;
  this.isCancelling = false;
  this.state.clearBatch();
  const currentApprovalMode = this.config.getApprovalMode();

  try {
    const toolRegistry = this.config.getToolRegistry();
    const newCalls: ToolCall[] = requests.map((request) => {
      // ... 创建 ToolCall 对象
    });

    this.state.enqueue(newCalls);
    await this._processQueue(signal);
    return this.state.completedBatch;
  } finally {
    this.isProcessing = false;
    this.state.clearBatch();
    this._processNextInRequestQueue();
  }
}
```

</details>

### 5.3 关键调用链

```text
schedule(requests)              [packages/core/src/scheduler/scheduler.ts:169]
  -> _enqueueRequest()          [packages/core/src/scheduler/scheduler.ts:180] (若忙)
  -> _startBatch(requests)      [packages/core/src/scheduler/scheduler.ts:183] (若空闲)
    -> state.enqueue(newCalls)  [packages/core/src/scheduler/scheduler.ts:301]
    -> _processQueue(signal)    [packages/core/src/scheduler/scheduler.ts:302]
      -> _processNextItem()     [packages/core/src/scheduler/scheduler.ts:375]
        - 检查 state.isActive
        - dequeue 一个 call
        - _processValidatingCall() [packages/core/src/scheduler/scheduler.ts:494]
        - _execute(call)        [packages/core/src/scheduler/scheduler.ts:600]
          - 状态流转: Validating -> Scheduled -> Executing
          - 实际工具执行
          - 状态流转: -> Success/Error/Cancelled
          - finalizeCall
```

---

## 6. 设计意图与 Trade-off

### 6.1 Gemini CLI 的选择

| 维度 | Gemini CLI 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 执行模式 | 串行执行 | 并行执行 | 简单可预测，避免资源竞争，但牺牲执行效率 |
| 状态管理 | 显式状态机 | 隐式 Promise | 状态可追踪、可恢复，但代码复杂度增加 |
| 队列策略 | 双层队列缓冲 | 直接执行 | 解耦请求与执行，支持异步调度 |
| 结果处理 | 批量返回 | 即时回调 | 统一返回结果数组，便于 Agent Loop 处理 |

### 6.2 为什么这样设计？

**核心问题**：为什么 Gemini CLI 选择串行而非并行执行工具调用？

**Gemini CLI 的解决方案**：

- **代码依据**：`packages/core/src/scheduler/scheduler.ts:373-378`
- **设计意图**：通过 `isActive` 检查强制单活跃调用，确保工具执行的可预测性和确定性
- **带来的好处**：
  - 避免资源竞争：文件、网络等资源不会被并发访问
  - 简化错误处理：一个工具失败不会影响其他工具
  - 结果确定性：按请求顺序返回结果，便于 LLM 理解
- **付出的代价**：
  - 执行效率：无法利用并行 I/O 提升性能
  - 延迟累积：多个工具的总耗时 = 各工具耗时之和

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统串行"
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "单active call + 状态机"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "并发派发 + 顺序收集"
    checkout main
    branch "Codex"
    checkout "Codex"
    commit id: "Actor消息驱动"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "resetTimeoutOnProgress"
```

| 项目 | 并发策略 | 核心差异 | 适用场景 |
|-----|---------|---------|---------|
| **Gemini CLI** | 串行执行 | 单 active call + 7 状态状态机 | 需要严格顺序保证、资源安全 |
| **Kimi CLI** | 并发派发、顺序收集 | 同时发起多个调用，结果按序注入 | 需要并行 I/O 提升效率 |
| **Codex** | Actor 消息驱动 | Rust 异步特性，消息队列调度 | 高性能异步场景 |
| **OpenCode** | 超时重置机制 | `resetTimeoutOnProgress` 长任务支持 | 长时间运行任务 |
| **SWE-agent** | 顺序执行 | `forward_with_handling()` 错误恢复 | 错误恢复优先 |

**对比分析**：

- **Gemini CLI 的串行策略**更适合需要严格顺序保证的场景，如文件修改后紧接着读取同一文件
- **Kimi CLI 的并发策略**更适合 I/O 密集型场景，如同时读取多个不相关文件
- 两者都在结果返回给 LLM 时保持顺序，确保 LLM 能正确匹配请求与结果

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 队列处理完成 | `state.queueLength === 0` 且 `!state.isActive` | `packages/core/src/scheduler/scheduler.ts:373-378` |
| 用户取消 | 调用 cancelAll() 方法 | `packages/core/src/scheduler/scheduler.ts:225-249` |
| 信号中断 | `signal.aborted` 为 true | `packages/core/src/scheduler/scheduler.ts:385-388` |
| 会话结束 | Agent Loop 终止 | 外部触发 |

### 7.2 超时/资源限制

```typescript
// ⚠️ Inferred: 工具执行超时由具体工具执行器控制
// 而非在 Scheduler 层统一处理
// 详见 packages/core/src/scheduler/tool-executor.ts
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 工具执行错误 | 状态流转为 Error，继续处理队列 | `packages/core/src/scheduler/scheduler.ts:494-522` |
| 验证失败 | 状态流转为 Error，不进入执行阶段 | `packages/core/src/scheduler/scheduler.ts:329-369` |
| 审批拒绝 | 状态流转为 Cancelled，跳过执行 | `packages/core/src/scheduler/scheduler.ts:582-590` |
| 策略拒绝 | 状态流转为 Error，返回拒绝信息 | `packages/core/src/scheduler/scheduler.ts:535-551` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `packages/core/src/scheduler/scheduler.ts` | 169 | `schedule()` 方法，接收工具请求 |
| 批量启动 | `packages/core/src/scheduler/scheduler.ts` | 265 | `_startBatch()` 转换并入队 |
| 核心循环 | `packages/core/src/scheduler/scheduler.ts` | 373 | `_processQueue()` 调度循环 |
| 单步执行 | `packages/core/src/scheduler/scheduler.ts` | 384 | `_processNextItem()` 串行执行 |
| 工具执行 | `packages/core/src/scheduler/scheduler.ts` | 600 | `_execute()` 实际执行工具 |
| 完成检查 | `packages/core/src/scheduler/scheduler.ts` | 946 | `checkAndNotifyCompletion()` |
| 数据结构 | `packages/core/src/scheduler/types.ts` | 35 | `ToolCallRequestInfo` 定义 |
| 状态枚举 | `packages/core/src/scheduler/types.ts` | 25 | `CoreToolCallStatus` 状态机 |
| 兼容层 | `packages/core/src/core/coreToolScheduler.ts` | 98 | `CoreToolScheduler` 类 |

---

## 9. 延伸阅读

- 前置知识：`docs/gemini-cli/04-gemini-cli-agent-loop.md` - Agent Loop 整体架构
- 相关机制：`docs/gemini-cli/05-gemini-cli-tools-system.md` - 工具系统详解
- 深度分析：`docs/kimi-cli/questions/kimi-cli-tool-call-concurrency.md` - Kimi CLI 的并发策略对比

---

*✅ Verified: 基于 gemini-cli/packages/core/src/scheduler/scheduler.ts 等源码分析*
*⚠️ Inferred: 部分设计意图基于代码结构推断*
*基于版本：2026-02-08 | 最后更新：2026-03-03*
