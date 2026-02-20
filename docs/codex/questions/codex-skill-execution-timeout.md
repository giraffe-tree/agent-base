# Codex Skill 执行超时机制

## 结论

Codex 采用**分层超时控制**策略：MCP 服务器启动超时（默认 30s）+ 工具执行超时（默认 10 分钟），通过 Rust 的 `CancellationToken` 实现异步取消，超时后通过 `EventMsg::Error` 上报并结束当前 turn，但会话保持存活。

---

## 关键代码位置

| 层级 | 文件路径 | 关键职责 |
|-----|---------|---------|
| 配置定义 | `codex-rs/mcp/src/mcp_server_config.rs` | `McpServerConfig` 结构体定义 |
| 配置定义 | `codex-rs/mcp/src/mcp_runtime.rs` | 超时参数解析与应用 |
| Shell 执行 | `codex-rs/terminal/src/local_shell.rs` | `LocalShellCall` 执行与超时处理 |
| Agent 循环 | `codex-rs/core/src/agent_loop.rs` | 取消信号传递与错误处理 |
| 事件上报 | `codex-rs/core/src/protocol.rs` | `EventMsg::Error` 定义 |

---

## 流程图

### 完整超时判断流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        Skill 执行超时流程                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │  用户调用工具 │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│          ▼                                                      │
│   ┌──────────────────┐                                          │
│   │ 读取 McpServerConfig│                                         │
│   │ startup_timeout: 30s│                                         │
│   │ tool_timeout: 10min │                                         │
│   └────────┬─────────┘                                          │
│            │                                                    │
│            ▼                                                    │
│   ┌──────────────────┐                                          │
│   │ 创建 CancellationToken │                                      │
│   └────────┬─────────┘                                          │
│            │                                                    │
│            ▼                                                    │
│   ┌─────────────────────────────────────────┐                   │
│   │         tokio::time::timeout            │                   │
│   │  (params.timeout_ms 或默认 tool_timeout) │                   │
│   └───────────────┬─────────────────────────┘                   │
│                   │                                             │
│         ┌─────────┴─────────┐                                   │
│         │                   │                                   │
│         ▼                   ▼                                   │
│   ┌───────────┐      ┌──────────────┐                          │
│   │  正常完成   │      │   超时触发    │                          │
│   └─────┬─────┘      └──────┬───────┘                          │
│         │                   │                                   │
│         ▼                   ▼                                   │
│   ┌───────────┐      ┌──────────────────┐                      │
│   │返回成功结果 │      │ 取消 token 触发   │                      │
│   └───────────┘      └────────┬─────────┘                      │
│                               │                                 │
│                               ▼                                 │
│                       ┌──────────────────┐                     │
│                       │  终止所有子任务    │                     │
│                       └────────┬─────────┘                     │
│                                │                                │
│                                ▼                                │
│                       ┌──────────────────┐                     │
│                       │ 构造 Timeout 错误  │                     │
│                       │ ErrorCode: 1001   │                     │
│                       └────────┬─────────┘                     │
│                                │                                │
│                                ▼                                │
│                       ┌──────────────────┐                     │
│                       │ EventMsg::Error   │                     │
│                       │ 发送给前端        │                     │
│                       └────────┬─────────┘                     │
│                                │                                │
│                                ▼                                │
│                       ┌──────────────────┐                     │
│                       │ 当前 turn 结束    │                     │
│                       │ 会话继续可用      │                     │
│                       └──────────────────┘                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 超时配置体系

### 1. 配置层

**`McpServerConfig` 结构体**（`codex-rs/mcp/src/mcp_server_config.rs`）

```rust
pub struct McpServerConfig {
    /// MCP 服务器启动超时（默认 30 秒）
    pub startup_timeout_sec: Option<Duration>,

    /// 工具执行超时（默认 10 分钟）
    pub tool_timeout_sec: Option<Duration>,

    /// 允许使用的工具白名单
    pub enabled_tools: Option<Vec<String>>,

    /// 禁止使用的工具黑名单
    pub disabled_tools: Option<Vec<String>>,
}
```

**配置加载流程**（`codex-rs/mcp/src/mcp_runtime.rs:45-80`）

```rust
impl McpRuntime {
    pub fn new(config: McpServerConfig) -> Self {
        let startup_timeout = config.startup_timeout_sec
            .unwrap_or(Duration::from_secs(30));
        let tool_timeout = config.tool_timeout_sec
            .unwrap_or(Duration::from_secs(600)); // 10 分钟

        Self {
            startup_timeout,
            tool_timeout,
            // ...
        }
    }
}
```

### 2. 执行层

**`LocalShellCall` 执行与超时**（`codex-rs/terminal/src/local_shell.rs:120-180`）

```rust
impl LocalShellCall {
    async fn execute(&self, action: LocalShellAction) -> LocalShellResult {
        match action {
            LocalShellAction::Exec(exec) => {
                let params = ShellToolCallParams {
                    command: exec.command,
                    timeout_ms: exec.timeout_ms,  // 执行时传入的超时参数
                    sandbox_permissions: Some(SandboxPermissions::UseDefault),
                };

                // 实际执行带超时的命令
                match tokio::time::timeout(
                    Duration::from_millis(params.timeout_ms),
                    self.run_command(params)
                ).await {
                    Ok(result) => result,
                    Err(_) => LocalShellResult::Timeout {
                        message: format!("Command timed out after {}ms", params.timeout_ms),
                    },
                }
            }
            // ...
        }
    }
}
```

### 3. 取消机制

**取消信号传递**（`codex-rs/core/src/agent_loop.rs:200-250`）

```rust
pub struct AgentLoop {
    cancel_token: CancellationToken,
    // ...
}

impl AgentLoop {
    /// 处理用户中断或超时取消
    async fn handle_interrupt(&self, op: Op) -> Result<()> {
        match op {
            Op::Interrupt => {
                // 取消所有正在执行的任务
                self.cancel_token.cancel();
                self.abort_all_tasks().await;

                // 发送中断事件给前端
                self.send_event(EventMsg::Error {
                    message: "Execution interrupted".to_string(),
                    code: ErrorCode::ExecutionInterrupted,
                }).await;

                Ok(())
            }
            // ...
        }
    }
}
```

---

## 超时后的行为

### 错误类型

**`EventMsg::Error` 结构**（`codex-rs/core/src/protocol.rs:80-120`）

```rust
pub enum EventMsg {
    /// 执行错误（包含超时）
    Error {
        message: String,
        code: ErrorCode,
    },
    /// 工具执行结果
    ToolResult { ... },
    /// 其他事件...
}

pub enum ErrorCode {
    ExecutionTimeout = 1001,
    ExecutionInterrupted = 1002,
    ToolExecutionFailed = 1003,
    // ...
}
```

### 恢复策略

```
┌─────────────────────────────────────────────────────────────┐
│                     超时处理流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 执行命令                                                 │
│     │                                                       │
│     ▼                                                       │
│  ┌─────────────────┐    超时    ┌─────────────────────┐     │
│  │ tokio::timeout  │───────────▶│ 返回 Timeout 错误    │     │
│  │                 │           │ LocalShellResult::  │     │
│  │                 │───────────▶│ Timeout             │     │
│  └─────────────────┘   正常完成   └──────────┬──────────┘     │
│     │                                       │                │
│     │                                       ▼                │
│     │                          ┌─────────────────────────┐   │
│     │                          │ AgentLoop 捕获错误       │   │
│     │                          │ 生成 EventMsg::Error    │   │
│     │                          └──────────┬──────────────┘   │
│     │                                     │                  │
│     ▼                                     ▼                  │
│  正常结果                          发送给前端                  │
│                             当前 turn 结束                   │
│                             会话保持存活                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 数据流转

```
配置文件 (mcp-server.toml)
    │
    │ startup_timeout_sec = 60
    │ tool_timeout_sec = 600
    ▼
McpServerConfig 结构体
    │
    ├───▶ startup_timeout: Duration
    └───▶ tool_timeout: Duration
              │
              ▼
    ┌─────────────────────┐
    │   MCP Runtime 初始化  │
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  用户发起工具调用     │
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │ LocalShellCall::Exec │
    │  timeout_ms 参数     │
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │ tokio::time::timeout │
    │ 异步等待执行结果      │
    └──────────┬──────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
   成功完成          超时/取消
       │               │
       ▼               ▼
   Ok(result)    Err(Elapsed)
       │               │
       ▼               ▼
   ToolResult     EventMsg::Error
                       │
                       ▼
                  ErrorCode::ExecutionTimeout
```

---

## 配置示例

**`~/.codex/mcp-server.toml`**

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
startup_timeout_sec = 60      # 服务器启动最多等待 60 秒
tool_timeout_sec = 600        # 工具执行最多 10 分钟

[[mcp_servers]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
startup_timeout_sec = 30      # GitHub API 启动较快，30 秒足够
tool_timeout_sec = 300        # API 调用通常较快，5 分钟
enabled_tools = ["search_issues", "create_issue"]  # 只启用特定工具
```

---

## 设计亮点

1. **分层超时**：启动超时与执行超时分离，避免慢启动服务影响整体
2. **CancellationToken**：Rust 标准异步取消机制，资源清理可靠
3. **优雅降级**：超时仅结束当前 turn，不中断整个会话
4. **配置灵活**：支持全局默认 + 单个 MCP 服务器自定义

---

> **版本信息**：基于 Codex 2026-02-08 版本源码
