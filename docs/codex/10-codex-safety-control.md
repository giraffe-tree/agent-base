# Safety Control（codex）

结论先行：codex 的 safety-control 采用“策略配置 + 执行前门控 + 审批分支 + 失败分级”的分层设计。控制主线不是单点拦截器，而是从 `ToolsConfig/MCP 配置` 注入到 `TurnContext`，在 `ToolRouter/ToolRegistry` 与 MCP 调用路径上多点生效。

> 说明：当前仓库内 `codex` 实码未完整收录，本文依据 `docs/codex/*` 中已提取的源码级引用整理。

---

## 1. 跨项目统一安全控制流程图

```text
+--------------------------------------------+
| 用户请求 User Request                      |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 策略来源 Policy Source                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 执行前检查 Pre-Execution Check             |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 审批闸门 Approval Gate                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 工具执行 Tool Execution                    |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 边界/沙箱 Boundary and Sandbox Guard       |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 结果/错误 Result or Error                  |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 重试/中止/降级 Retry, Abort, or Fallback   |
+--------------------------------------------+
```

---

## 2. codex 项目级控制链路

```text
[配置策略 ToolsConfig + MCP Server Config]
                    |
                    v
[回合上下文 Turn Context]
                    |
                    v
[路由分发 Tool Router Dispatch]
                    |
                    v
[注册表门控 Tool Registry Dispatch]
                    |
                    v
[类型/变更检查 Type and Mutating Check]
                    |
                    v
[并发门控 tool_call_gate]
                    |
                    v
[MCP 审批请求 MCP Approval Request]
                    |
                    v
               +-----------------------------+
               | 接受或拒绝 Accept / Decline |
               +-------------+---------------+
                             |
             +---------------+----------------+
             |                                |
             v                                v
[执行工具 session.call_tool]      [拒绝结果返回 Rejected Result]
             |                                |
             +---------------+----------------+
                             v
          [错误分级 Function Call Error Classify]
                             |
                             v
        [继续或中止 Turn Continue or Abort]
```

---

## 3. 策略来源（Policy Source）

- `ToolsConfig`：控制 shell 工具类型、仅 JS REPL 模式、实验工具集合。
- `McpServerConfig`：控制 MCP `enabled_tools/disabled_tools/scopes/tool_timeout_sec`。
- `TurnContext`：把 `sandbox/approval/cwd/model` 注入单个 turn，使后续 dispatch 基于同一上下文判定。

核心点：策略不是只在启动时读取一次，而是被带入每次 turn 的工具执行路径。

---

## 4. 执行前检查与审批

- `ToolRouter.dispatch_tool_call()`：在 `js_repl_tools_only` 等模式下提前阻断不合规 direct tool call。
- `ToolRegistry.dispatch()`：做 `matches_kind`、`is_mutating` 与并发门控（`tool_call_gate.wait_ready()`）。
- MCP 工具调用进入 `handle_mcp_tool_call()` 后，会先走 `maybe_request_mcp_tool_approval()`：
  - `Accept`：继续 `session.call_tool()`
  - `Decline`：直接构造拒绝结果返回模型

---

## 5. 权限边界与失败处理

- 本地 shell 调用映射为 `SandboxPermissions::UseDefault`，避免模型在 tool payload 中任意放大权限。
- MCP 远端能力通过 server 级 allow/deny 与 scope 约束，避免“连上即全开”。
- 错误分级：
  - `FunctionCallError::Fatal`：中止
  - `FunctionCallError::RespondToModel`：把失败回传给模型并继续 loop
- Hook 失败分级：
  - `FailedContinue`：记警告继续
  - `FailedAbort`：中止回合

---

## 6. Linux Proxy-Only 网络沙箱 (2026-02)

Codex 新增 Linux 专用代理沙箱模式，通过 TCP-UDS-TCP 桥接实现仅代理网络访问。

### 6.1 架构设计

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

沙箱内通过 UDS 与主机通信，避免直接访问网络
```

### 6.2 代码实现

```rust
// codex/codex-rs/linux-sandbox/src/proxy_routing.rs:70-119

/// 准备主机代理路由配置
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

### 6.3 Seccomp 模式配合

代理沙箱可与 Seccomp 模式配合使用，提供多层防护：

1. **网络层**: 仅允许通过 UDS 与主机代理通信
2. **系统调用层**: Seccomp 过滤器限制可用系统调用

### 6.4 支持的代理环境变量

```rust
// proxy_routing.rs:26-41
const PROXY_ENV_KEYS: &[&str] = &[
    “HTTP_PROXY”, “HTTPS_PROXY”, “ALL_PROXY”, “FTP_PROXY”,
    “YARN_HTTP_PROXY”, “YARN_HTTPS_PROXY”,
    “NPM_CONFIG_HTTP_PROXY”, “NPM_CONFIG_HTTPS_PROXY”, “NPM_CONFIG_PROXY”,
    “BUNDLE_HTTP_PROXY”, “BUNDLE_HTTPS_PROXY”,
    “PIP_PROXY”,
    “DOCKER_HTTP_PROXY”, “DOCKER_HTTPS_PROXY”,
];
```

---

## 7. Reject 审批策略 (2026-02)

Codex 新增 RejectConfig 配置，用于自动拒绝特定类型的审批请求。

### 7.1 RejectConfig 定义

```typescript
// codex/codex-rs/app-server-protocol/schema/typescript/RejectConfig.ts

export type RejectConfig = {
    /**
     * Reject approval prompts related to sandbox escalation.
     */
    sandbox_approval: boolean,
    /**
     * Reject prompts triggered by execpolicy `prompt` rules.
     */
    rules: boolean,
    /**
     * Reject MCP elicitation prompts.
     */
    mcp_elicitations: boolean,
};
```

### 7.2 使用场景

| 配置项 | 用途 | 典型场景 |
|--------|------|----------|
| `sandbox_approval` | 拒绝沙箱升级请求 | 禁止 AI 请求更高权限 |
| `rules` | 拒绝执行策略触发的确认 | 自动拒绝敏感命令 |
| `mcp_elicitations` | 拒绝 MCP 工具授权请求 | 限制外部工具使用 |

### 7.3 Rust 侧集成点

RejectConfig 在 Rust 侧通过 `AskForApproval::Reject` 变体实现，分布在多个关键路径：

**定义位置**：`protocol/src/protocol.rs`

```rust
pub enum AskForApproval {
    Never,
    OnFailure,
    OnRequest,
    UnlessTrusted,
    Reject(RejectConfig),  // 新增变体
}

pub struct RejectConfig {
    pub sandbox_approval: bool,
    pub rules: bool,
    pub mcp_elicitations: bool,
}
```

**集成点 1：补丁安全评估** (`core/src/safety.rs:54-58`)
```rust
let rejects_sandbox_approval = matches!(policy, AskForApproval::Never)
    || matches!(
        policy,
        AskForApproval::Reject(reject_config) if reject_config.sandbox_approval
    );
```

**集成点 2：执行策略检查** (`core/src/exec_policy.rs:113-123`)
```rust
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

**集成点 3：MCP 引导请求** (`core/src/mcp_connection_manager.rs:245`)
```rust
AskForApproval::Reject(reject_config) => reject_config.rejects_mcp_elicitations(),
```

**集成点 4：工具沙箱编排** (`core/src/tools/sandboxing.rs:178`)
```rust
if needs_approval && matches!(
    policy,
    AskForApproval::Reject(reject_config) if reject_config.rejects_sandbox_approval()
) {
    return ExecApprovalRequirement::Forbidden { ... };
}
```

**设计意图**：RejectConfig 不是 TurnContext 的字段，而是通过 `AskForApproval::Reject` 变体贯穿整个审批流程，确保所有审批入口都遵守拒绝策略。

---

## 8. 证据索引（项目名 + 文件路径 + 关键职责）

- `codex` + `docs/codex/04-codex-agent-loop.md` + turn 主链路、`TurnContext` 字段注入与运行边界。
- `codex` + `docs/codex/05-codex-tools-system.md` + `ToolsConfig`、`ToolRouter/ToolRegistry` 门控与错误分级。
- `codex` + `docs/codex/06-codex-mcp-integration.md` + MCP 配置面与 `maybe_request_mcp_tool_approval()` 审批分支。
- `codex` + `docs/codex/questions/codex-tool-call-concurrency.md` + 并发工具调用控制与 mutating 工具串行化。
- `codex` + `codex/codex-rs/linux-sandbox/src/proxy_routing.rs` + Linux Proxy-Only 沙箱 TCP-UDS-TCP 桥接实现。
- `codex` + `codex/codex-rs/app-server-protocol/schema/typescript/RejectConfig.ts` + Reject 审批策略配置定义。

---

## 9. 边界与不确定性

- 本文结论基于仓库内研究文档中的源码引用，不是直接在本仓库编译运行 `codex-rs` 得出。
- 若后续引入 `codex` 实码镜像，建议补一节”配置键值到 Rust 结构体字段”的逐项映射核对。
- Proxy-Only 沙箱仅适用于 Linux 平台，macOS/Windows 使用其他沙箱机制。


- **✅ Verified**: RejectConfig Rust 集成点（`safety.rs:57`, `exec_policy.rs:113`, `mcp_connection_manager.rs:245`, `sandboxing.rs:178`）
