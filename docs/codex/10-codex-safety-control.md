# Safety Control（codex）

## TL;DR（结论先行）

一句话定义：Codex 的 Safety Control 是**策略配置 + 执行前门控 + 审批分支 + 失败分级**的分层安全框架，从配置注入到工具执行多点生效。

Codex 的核心取舍：**中心化策略注入 + 显式审批门控**（对比 Gemini CLI 的静态分析、Kimi CLI 的自动确认超时）

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

没有 Safety Control：
```
模型执行危险命令 → 无检查直接执行 → 数据丢失/系统损坏
MCP 工具过度授权 → 无审批直接调用 → 敏感信息泄露
沙箱配置错误 → 无验证直接运行 → 安全边界突破
```

有 Safety Control：
```
危险命令 → 策略检查 → 审批请求 → 用户确认 → 执行
MCP 调用 → 作用域验证 → 授权检查 → 受控执行 → 返回
沙箱启动 → 配置验证 → Seccomp 加载 → 网络隔离 → 安全运行
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 策略一致性 | 不同入口策略不统一，出现安全漏洞 |
| 审批用户体验 | 过度审批导致用户疲劳，降低效率 |
| MCP 工具隔离 | 外部工具过度授权，风险不可控 |
| 错误分级 | 所有错误都中止，影响正常流程 |
| 沙箱绕过 | 沙箱配置被绕过，隔离失效 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Tool System                                     │
│ codex-rs/core/src/loop.rs                                    │
│ codex-rs/core/src/tools/                                     │
└───────────────────────┬─────────────────────────────────────┘
                        │ 调用工具
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Safety Control ▓▓▓                                      │
│ codex-rs/core/src/                                           │
│ - safety.rs       : 补丁安全评估                             │
│ - exec_policy.rs  : 执行策略检查                             │
│ - sandboxing.rs   : 沙箱编排                                 │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ ToolsConfig  │ │ RejectConfig │ │ Sandbox      │
│ 工具配置     │ │ 拒绝策略     │ │ 沙箱实现     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `AskForApproval` | 审批策略枚举 | `protocol/src/protocol.rs` |
| `RejectConfig` | 自动拒绝配置 | `protocol/src/protocol.rs` |
| `SafetyChecker` | 补丁安全评估 | `core/src/safety.rs:54` |
| `ExecPolicy` | 执行策略检查 | `core/src/exec_policy.rs:113` |
| `SandboxOrchestrator` | 沙箱权限编排 | `core/src/tools/sandboxing.rs:178` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant P as ExecPolicy
    participant S as SafetyChecker
    participant M as McpManager
    participant U as User

    A->>P: 检查执行策略
    P->>P: 匹配规则

    alt 需要审批
        P->>U: 请求确认
        U-->>P: Accept/Decline
    else 自动拒绝
        P->>P: RejectConfig 检查
        Note over P: sandbox_approval/rules/mcp_elicitations
    end

    P-->>A: 审批结果

    alt 补丁操作
        A->>S: 评估补丁安全
        S->>S: 检查文件变更
        S-->>A: 安全评估结果
    end

    alt MCP 调用
        A->>M: 检查 MCP 授权
        M->>M: scope/allow/deny 验证
        M-->>A: 授权结果
    end
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1-2 | 执行策略检查 | 统一入口，集中策略判断 |
| 3-5 | 审批/拒绝分支 | 支持交互式确认和自动拒绝两种模式 |
| 6-8 | 补丁安全评估 | 文件操作前进行风险分析 |
| 9-11 | MCP 授权检查 | 外部工具权限隔离 |

---

## 3. 核心组件详细分析

### 3.1 AskForApproval 与 RejectConfig 内部结构

#### 职责定位

`AskForApproval` 是审批策略的核心枚举，控制何时向用户请求确认。`RejectConfig` 提供细粒度的自动拒绝能力。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Never: 完全自动
    [*] --> OnFailure: 仅失败时
    [*] --> OnRequest: 每次请求
    [*] --> UnlessTrusted: 可信时自动
    [*] --> Reject: 自动拒绝

    Never --> [*]: 无需审批
    OnFailure --> Confirm: 失败发生
    OnRequest --> Confirm: 每次执行
    UnlessTrusted --> CheckTrust: 检查信任
    Reject --> CheckType: 根据类型拒绝

    CheckTrust --> [*]: 可信
    CheckTrust --> Confirm: 不可信

    CheckType --> [*]: sandbox_approval
    CheckType --> [*]: rules
    CheckType --> [*]: mcp_elicitations

    Confirm --> Accept: 用户接受
    Confirm --> Decline: 用户拒绝
    Accept --> [*]
    Decline --> [*]
```

#### 关键算法逻辑

```rust
// protocol/src/protocol.rs

pub enum AskForApproval {
    Never,                    // 完全自动，不询问
    OnFailure,               // 仅失败时询问
    OnRequest,               // 每次请求都询问
    UnlessTrusted,           // 除非被信任
    Reject(RejectConfig),    // 自动拒绝配置
}

pub struct RejectConfig {
    pub sandbox_approval: bool,     // 拒绝沙箱升级
    pub rules: bool,                // 拒绝策略触发的确认
    pub mcp_elicitations: bool,     // 拒绝 MCP 引导请求
}
```

**算法要点**：

1. **策略分层**：从完全自动到完全拒绝的五级策略
2. **细粒度控制**：RejectConfig 针对三类场景独立配置
3. **贯穿全链路**：同一策略在多个检查点生效

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `rejects_sandbox_approval()` | - | bool | 是否拒绝沙箱审批 | `protocol.rs` |
| `rejects_rules_approval()` | - | bool | 是否拒绝规则审批 | `protocol.rs` |
| `rejects_mcp_elicitations()` | - | bool | 是否拒绝 MCP 引导 | `protocol.rs` |

### 3.2 ExecPolicy 执行策略内部结构

#### 职责定位

ExecPolicy 负责根据配置和规则判断命令执行前的审批要求。

#### 关键算法逻辑

```rust
// core/src/exec_policy.rs:113-123

AskForApproval::Reject(reject_config) => {
    if prompt_is_rule {
        if reject_config.rejects_rules_approval() {
            Some(REJECT_RULES_APPROVAL_REASON)
        }
    } else if reject_config.rejects_sandbox_approval() {
        Some(REJECT_SANDBOX_APPROVAL_REASON)
    }
}
```

**代码要点**：

1. **规则区分**：区分策略触发(rule)和沙箱升级(sandbox)两类审批
2. **提前拦截**：在执行前返回拒绝原因，避免危险操作
3. **统一理由**：标准化拒绝理由，便于日志和调试

### 3.3 SandboxOrchestrator 内部结构

#### 职责定位

SandboxOrchestrator 负责沙箱权限的编排，确保工具在受控环境中执行。

#### 关键算法逻辑

```rust
// core/src/tools/sandboxing.rs:178

if needs_approval && matches!(
    policy,
    AskForApproval::Reject(reject_config) if reject_config.rejects_sandbox_approval()
) {
    return ExecApprovalRequirement::Forbidden {
        reason: "Sandbox approval rejected by configuration",
    };
}
```

### 3.4 Linux Proxy-Only 网络沙箱

#### 架构设计

```text
主机网络命名空间                    沙箱网络命名空间
┌─────────────────┐                ┌─────────────────┐
│ 代理服务器       │◄──────────────►│ 本地桥接        │
│ (127.0.0.1:xxx) │   TCP 连接     │ (127.0.0.1:yyy) │
└────────┬────────┘                └────────┬────────┘
         │                                   │
         │    主机桥接进程 (host bridge)     │
         │    - Unix Domain Socket 监听      │
         │    - TCP 连接到代理服务器          │
         └───────────────────────────────────┘
```

#### 关键代码

```rust
// linux-sandbox/src/proxy_routing.rs:70-119

pub(crate) fn prepare_host_proxy_route_spec() -> io::Result<String> {
    let env: HashMap<String, String> = std::env::vars().collect();
    let plan = plan_proxy_routes(&env);

    // 创建 UDS 目录
    let socket_dir = create_proxy_socket_dir()?;

    // 为每个唯一端点创建主机桥接进程
    for (endpoint, socket_path) in &socket_by_endpoint {
        host_bridge_pids.push(spawn_host_bridge(*endpoint, socket_path)?);
    }

    // 生成路由配置 JSON
    serde_json::to_string(&ProxyRouteSpec { routes }).map_err(io::Error::other)
}
```

### 3.5 组件间协作时序

```mermaid
sequenceDiagram
    participant U as Agent Loop
    participant T as ToolRouter
    participant R as ToolRegistry
    participant P as ExecPolicy
    participant S as Sandbox

    U->>T: dispatch_tool_call()
    T->>T: js_repl_tools_only 检查

    alt 模式阻止
        T-->>U: 返回错误
    else 允许通过
        T->>R: registry.dispatch()
        R->>R: matches_kind() 验证
        R->>R: is_mutating() 检查
        R->>R: tool_call_gate.wait_ready()

        R->>P: 执行策略检查
        alt RejectConfig 匹配
            P-->>R: 返回拒绝
        else 需要审批
            P->>U: 请求确认
            U-->>P: 审批结果
        end

        P-->>R: 审批结果
        R->>S: 沙箱权限编排
        S-->>R: 执行环境
        R-->>U: 执行结果
    end
```

### 3.6 关键数据路径

#### 主路径（正常审批）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[ToolsConfig] --> I2[McpServerConfig]
    end

    subgraph Process["处理阶段"]
        P1[TurnContext 注入] --> P2[策略匹配]
        P2 --> P3{AskForApproval}
        P3 -->|Never| P4[自动执行]
        P3 -->|OnRequest| P5[请求确认]
        P3 -->|Reject| P6[自动拒绝]
    end

    subgraph Output["输出阶段"]
        O1[执行/拒绝] --> O2[结果返回]
    end

    I2 --> P1
    P4 --> O1
    P5 --> O1
    P6 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误分级）

```mermaid
flowchart TD
    E[执行错误] --> E1{错误类型}
    E1 -->|Fatal| R1[中止 Turn]
    E1 -->|RespondToModel| R2[返回错误给模型]
    E1 -->|Hook FailedContinue| R3[记录警告继续]
    E1 -->|Hook FailedAbort| R4[中止回合]

    style R1 fill:#FF6B6B
    style R2 fill:#FFD700
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as Agent Loop
    participant Router as ToolRouter
    participant Policy as ExecPolicy
    participant Sandbox as SandboxOrchestrator

    User->>Agent: 输入命令
    Agent->>Router: dispatch_tool_call()

    Router->>Router: js_repl_tools_only 检查
    alt 被模式阻止
        Router-->>Agent: 返回错误响应
    end

    Router->>Router: 构建 ToolInvocation
    Router->>Policy: 执行策略检查

    alt RejectConfig 匹配
        Policy-->>Router: 返回拒绝原因
        Router-->>Agent: 构造拒绝响应
    else 需要审批
        Policy->>User: 显示确认提示
        User-->>Policy: 接受/拒绝

        alt 拒绝
            Policy-->>Router: 返回拒绝
            Router-->>Agent: 构造拒绝响应
        end
    end

    Policy-->>Router: 审批通过
    Router->>Sandbox: 沙箱权限编排
    Sandbox-->>Router: 执行环境就绪

    Router->>Router: 执行工具
    Router-->>Agent: 返回结果
    Agent->>User: 显示输出
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 策略检查 | ToolInvocation | 匹配 ExecPolicy | ApprovalRequirement | `exec_policy.rs:113` |
| 审批请求 | ApprovalRequirement | 用户交互 | ApprovalDecision | `sandboxing.rs:178` |
| 沙箱编排 | ApprovalDecision | 权限配置 | SandboxConfig | `sandboxing.rs` |
| 错误分级 | ToolOutput | 分类处理 | Error/Continue | `registry.rs` |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Config["配置层"]
        C1[ToolsConfig]
        C2[McpServerConfig]
        C3[RejectConfig]
    end

    subgraph Control["控制层"]
        P1[ExecPolicy]
        P2[SafetyChecker]
        P3[SandboxOrchestrator]
    end

    subgraph Execution["执行层"]
        E1[ToolRouter]
        E2[ToolRegistry]
        E3[McpHandler]
    end

    C1 --> P1
    C2 --> P1
    C3 --> P1
    P1 --> E1
    P2 --> E1
    P3 --> E2
    E1 --> E2
    E2 --> E3
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```rust
// protocol/src/protocol.rs

pub enum AskForApproval {
    Never,
    OnFailure,
    OnRequest,
    UnlessTrusted,
    Reject(RejectConfig),
}

pub struct RejectConfig {
    pub sandbox_approval: bool,
    pub rules: bool,
    pub mcp_elicitations: bool,
}
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `sandbox_approval` | `bool` | 拒绝沙箱升级请求 |
| `rules` | `bool` | 拒绝策略触发的确认 |
| `mcp_elicitations` | `bool` | 拒绝 MCP 工具授权请求 |

### 5.2 主链路代码

```rust
// core/src/exec_policy.rs:113-123

AskForApproval::Reject(reject_config) => {
    if prompt_is_rule {
        if reject_config.rejects_rules_approval() {
            Some(REJECT_RULES_APPROVAL_REASON)
        }
    } else if reject_config.rejects_sandbox_approval() {
        Some(REJECT_SANDBOX_APPROVAL_REASON)
    }
}

// core/src/safety.rs:54-58
let rejects_sandbox_approval = matches!(policy, AskForApproval::Never)
    || matches!(
        policy,
        AskForApproval::Reject(reject_config) if reject_config.sandbox_approval
    );

// core/src/tools/sandboxing.rs:178
if needs_approval && matches!(
    policy,
    AskForApproval::Reject(reject_config) if reject_config.rejects_sandbox_approval()
) {
    return ExecApprovalRequirement::Forbidden { ... };
}
```

**代码要点**：

1. **统一枚举**：`AskForApproval::Reject` 贯穿所有审批检查点
2. **细粒度控制**：三类拒绝场景独立配置
3. **提前拦截**：在执行前返回拒绝，避免副作用

### 5.3 关键调用链

```text
dispatch_tool_call()          [tools/router.rs:372]
  -> js_repl_tools_only 检查
  -> ToolRegistry::dispatch()   [tools/registry.rs:194]
    -> is_mutating() 检查
    -> tool_call_gate.wait_ready()
    -> ExecPolicy 检查          [exec_policy.rs:113]
      - RejectConfig 匹配
      - 审批请求
    -> SandboxOrchestrator      [tools/sandboxing.rs:178]
      - 权限编排
      - needs_approval 检查
```

---

## 6. 设计意图与 Trade-off

### 6.1 Codex 的选择

| 维度 | Codex 的选择 | 替代方案 | 取舍分析 |
|-----|-------------|---------|---------|
| 策略注入 | TurnContext 注入 | 全局静态配置 | 每轮独立控制，但增加上下文传递 |
| 审批模式 | 显式交互式 | 自动确认超时 | 用户可控，但中断流程 |
| MCP 隔离 | Server 级 allow/deny | 无隔离/完全信任 | 粒度适中，配置复杂度适中 |
| 错误处理 | 分级错误类型 | 统一错误码 | 精确控制，但类型复杂 |
| 沙箱 | Linux Proxy-Only + Seccomp | 纯容器/无沙箱 | 轻量级，但平台受限 |

### 6.2 为什么这样设计？

**核心问题**：如何在保证安全的前提下，不牺牲用户体验？

**Codex 的解决方案**：
- 代码依据：`protocol/src/protocol.rs` 的 `AskForApproval` 枚举设计
- 设计意图：从完全自动到完全拒绝的五级策略，适应不同场景
- 带来的好处：
  - 可信环境可完全自动化
  - 敏感操作可强制确认
  - CI/自动化场景可配置拒绝
- 付出的代价：
  - 策略配置复杂度增加
  - 需要理解各类审批场景的区别

### 6.3 与其他项目的对比

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| Codex | 显式审批 + RejectConfig | 需要精细控制的企业环境 |
| Gemini CLI | 静态分析 + 自动确认超时 | 快速迭代，低摩擦 |
| Kimi CLI | 自动确认 + 超时回退 | 开发效率优先 |
| OpenCode | 基于策略的自动审批 | 规则明确的场景 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 策略拒绝 | RejectConfig 匹配 | `exec_policy.rs:113` |
| 用户拒绝 | 审批对话框选择 Decline | `sandboxing.rs` |
| Fatal 错误 | 严重执行错误 | `registry.rs` |
| Hook 中止 | after_tool_use 返回 FailedAbort | `registry.rs:545` |
| 沙箱配置错误 | 权限编排失败 | `sandboxing.rs` |

### 7.2 超时/资源限制

```rust
// MCP 工具调用超时
pub tool_timeout_sec: Option<Duration>,

// 服务器启动超时
pub startup_timeout_sec: Option<Duration>,
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| `RespondToModel` | 返回错误给模型，继续对话 | `router.rs:411` |
| `Fatal` | 中止当前 Turn | `router.rs:410` |
| `FailedContinue` | 记录警告，继续执行 | `registry.rs` |
| `FailedAbort` | 中止回合 | `registry.rs:545` |
| 审批拒绝 | 构造拒绝响应返回模型 | `sandboxing.rs` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 审批策略 | `protocol/src/protocol.rs` | - | AskForApproval 枚举 |
| 拒绝配置 | `protocol/src/protocol.rs` | 245 | RejectConfig 结构 |
| 执行策略 | `core/src/exec_policy.rs` | 113 | 策略检查逻辑 |
| 补丁安全 | `core/src/safety.rs` | 54 | 安全评估 |
| 沙箱编排 | `core/src/tools/sandboxing.rs` | 178 | 权限编排 |
| MCP 拒绝 | `core/src/mcp_connection_manager.rs` | 245 | MCP 引导检查 |
| 代理沙箱 | `linux-sandbox/src/proxy_routing.rs` | 70 | Proxy-Only 实现 |

---

## 9. 延伸阅读

- 前置知识：`05-codex-tools-system.md`、`06-codex-mcp-integration.md`
- 相关机制：`04-codex-agent-loop.md`
- 深度分析：`docs/codex/questions/codex-tool-security.md`

---

*✅ Verified: 基于 codex/codex-rs/core/src/{safety,exec_policy,sandboxing}.rs 源码分析*
*基于版本：2026-02-08 | 最后更新：2026-02-24*
