# Codex 工具调用错误处理机制

**结论先行**: Codex 采用 Rust 的类型安全优势，构建了**显式错误枚举 + 三档审批策略**的工具调用错误处理体系。核心特点是 `CodexErr` 枚举配合 `is_retryable()` 方法的显式可重试判定，以及 `SandboxErr` 对 Landlock+Seccomp 沙箱错误的精细封装，实现了企业级的安全与可靠性平衡。

---

## 1. 错误类型体系

### 1.1 主错误枚举 CodexErr

位于 `codex/codex-rs/core/src/error.rs`，Codex 使用 Rust 的 `thiserror` 宏定义了主错误枚举：

```rust
#[derive(Error, Debug)]
pub enum CodexErr {
    #[error("turn aborted. Something went wrong? Hit `/feedback` to report the issue.")]
    TurnAborted,

    /// SSE stream 断开，视为可重试错误
    #[error("stream disconnected before completion: {0}")]
    Stream(String, Option<Duration>),

    #[error("Codex ran out of room in the model's context window...")]
    ContextWindowExceeded,

    /// 配额耗尽 - 不可重试
    #[error("Quota exceeded. Check your plan and billing details.")]
    QuotaExceeded,

    /// 服务器过载 - 不可重试
    #[error("Selected model is at capacity. Please try a different model.")]
    ServerOverloaded,

    /// 沙箱错误
    #[error("sandbox error: {0}")]
    Sandbox(#[from] SandboxErr),

    /// 重试次数超限
    #[error("{0}")]
    RetryLimit(RetryLimitReachedError),

    // ... 其他错误变体
}
```

### 1.2 沙箱错误 SandboxErr

```rust
#[derive(Error, Debug)]
pub enum SandboxErr {
    /// 沙箱拒绝执行
    #[error("sandbox denied exec error...")]
    Denied {
        output: Box<ExecToolCallOutput>,
        network_policy_decision: Option<NetworkPolicyDecisionPayload>,
    },

    /// Seccomp 安装错误 (Linux)
    #[cfg(target_os = "linux")]
    #[error("seccomp setup error")]
    SeccompInstall(#[from] seccompiler::Error),

    /// 命令超时
    #[error("command timed out")]
    Timeout { output: Box<ExecToolCallOutput> },

    /// 信号终止
    #[error("command was killed by a signal")]
    Signal(i32),

    /// Landlock 限制不完全
    #[error("Landlock was not able to fully enforce all sandbox rules")]
    LandlockRestrict,
}
```

### 1.3 错误类型层级图

```
┌─────────────────────────────────────────────────────────────────┐
│                      CodexErr 错误体系                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐                                            │
│  │   CodexErr      │  主错误枚举（约20+变体）                     │
│  ├─────────────────┤                                            │
│  │ • TurnAborted   │  回合中止                                   │
│  │ • Stream        │  流断开（可重试）                            │
│  │ • ContextWindow │  上下文窗口超限（不可重试）                   │
│  │ • QuotaExceeded │  配额耗尽（不可重试）                        │
│  │ • ServerOverld  │  服务器过载（不可重试）                      │
│  │ • Sandbox(_)    │  沙箱错误（嵌套SandboxErr）                  │
│  │ • RetryLimit(_) │  重试超限                                   │
│  │ • Timeout       │  超时（可重试）                              │
│  │ • Interrupted   │  用户中断(Ctrl-C)                           │
│  │ • ...           │                                            │
│  └────────┬────────┘                                            │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────┐                                            │
│  │   SandboxErr    │  沙箱子错误枚举                              │
│  ├─────────────────┤                                            │
│  │ • Denied        │  沙箱拒绝 + 网络策略决策                      │
│  │ • Timeout       │  命令超时                                   │
│  │ • Signal(_)     │  信号终止                                   │
│  │ • Seccomp*      │  Seccomp错误(Linux)                         │
│  │ • LandlockRest  │  Landlock限制不完全                         │
│  └─────────────────┘                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 可重试错误判定机制

### 2.1 is_retryable() 显式枚举

Codex 的核心设计是**显式白名单**机制：

```rust
impl CodexErr {
    pub fn is_retryable(&self) -> bool {
        match self {
            // 不可重试错误（显式列出）
            CodexErr::TurnAborted
            | CodexErr::Interrupted
            | CodexErr::QuotaExceeded
            | CodexErr::InvalidRequest(_)
            | CodexErr::Sandbox(_)
            | CodexErr::RetryLimit(_)
            | CodexErr::ContextWindowExceeded
            | CodexErr::UsageLimitReached(_)
            | CodexErr::ServerOverloaded => false,

            // 可重试错误（显式列出）
            CodexErr::Stream(..)
            | CodexErr::Timeout
            | CodexErr::UnexpectedStatus(_)
            | CodexErr::ResponseStreamFailed(_)
            | CodexErr::ConnectionFailed(_)
            | CodexErr::InternalServerError
            | CodexErr::Io(_)
            | CodexErr::Json(_) => true,
            // ...
        }
    }
}
```

**关键设计决策**:
- `QuotaExceeded` 和 `ServerOverloaded` 明确标记为**不可重试**
- `Stream` 和 `Timeout` 明确标记为**可重试**
- `Sandbox` 错误整体不可重试，依赖审批流程

### 2.2 重试配置

```rust
// codex/codex-rs/core/src/model_provider_info.rs
pub struct ModelProviderInfo {
    /// SSE 流断开后的最大重试次数
    pub max_stream_retries: u32,  // 默认: 5

    /// HTTP 请求失败后的最大重试次数
    pub max_request_retries: u32,  // 默认: 4
}
```

---

## 3. 超时处理机制

### 3.1 ExecExpiration 统一抽象

位于 `codex/codex-rs/core/src/exec.rs`:

```rust
/// 终止 exec 调用的机制
#[derive(Clone, Debug)]
pub enum ExecExpiration {
    /// 指定超时时间
    Timeout(Duration),
    /// 使用默认超时(10秒)
    DefaultTimeout,
    /// 通过 CancellationToken 取消
    Cancellation(CancellationToken),
}

pub const DEFAULT_EXEC_COMMAND_TIMEOUT_MS: u64 = 10_000; // 10秒
```

### 3.2 超时实现

```rust
impl ExecExpiration {
    pub(crate) async fn wait(self) {
        match self {
            ExecExpiration::Timeout(duration) => tokio::time::sleep(duration).await,
            ExecExpiration::DefaultTimeout => {
                tokio::time::sleep(Duration::from_millis(DEFAULT_EXEC_COMMAND_TIMEOUT_MS)).await
            }
            ExecExpiration::Cancellation(cancel) => {
                cancel.cancelled().await;  // 等待 CancellationToken
            }
        }
    }
}
```

**特点**:
- 支持三种超时模式：固定超时、默认超时、CancellationToken
- 默认执行超时为 **10秒**
- 使用 Tokio 的异步 sleep 实现

---

## 4. 沙箱与权限错误处理

### 4.1 三档审批策略

位于 `codex/codex-rs/core/src/tools/sandboxing.rs`:

```rust
/// 指定工具调用需要何种审批
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum ExecApprovalRequirement {
    /// 无需审批
    Skip {
        /// 首次尝试跳过沙箱（由策略明确放行）
        bypass_sandbox: bool,
        /// 建议的 execpolicy 修正案
        proposed_execpolicy_amendment: Option<ExecPolicyAmendment>,
    },

    /// 需要审批
    NeedsApproval {
        reason: Option<String>,
        proposed_execpolicy_amendment: Option<ExecPolicyAmendment>,
    },

    /// 禁止执行
    Forbidden { reason: String },
}
```

### 4.2 审批策略生成

```rust
pub(crate) fn default_exec_approval_requirement(
    policy: AskForApproval,
    sandbox_policy: &SandboxPolicy,
) -> ExecApprovalRequirement {
    let needs_approval = match policy {
        AskForApproval::Never | AskForApproval::OnFailure => false,
        AskForApproval::OnRequest => !matches!(
            sandbox_policy,
            SandboxPolicy::DangerFullAccess | SandboxPolicy::ExternalSandbox { .. }
        ),
        AskForApproval::UnlessTrusted => true,
    };
    // ...
}
```

### 4.3 沙箱错误处理流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    Codex 沙箱错误处理流程                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                              │
│   │ 工具调用请求 │                                              │
│   └──────┬──────┘                                              │
│          ▼                                                     │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     │
│   │ 审批策略检查 │────▶│   Skip      │────▶│ 直接执行    │     │
│   └──────┬──────┘     └─────────────┘     └─────────────┘     │
│          │                                                     │
│          ├──────────▶ ┌─────────────┐     ┌─────────────┐     │
│          │            │  Forbidden  │────▶│ 返回错误    │     │
│          │            └─────────────┘     └─────────────┘     │
│          │                                                     │
│          └──────────▶ ┌─────────────┐     ┌─────────────┐     │
│                       │NeedsApproval│────▶│ 用户确认    │     │
│                       └─────────────┘     └──────┬──────┘     │
│                                                  │             │
│                                                  ▼             │
│                                           ┌─────────────┐     │
│                                           │ Landlock    │     │
│                                           │ + Seccomp   │     │
│                                           └──────┬──────┘     │
│                                                  │             │
│                              ┌───────────────────┼───────────┐│
│                              ▼                   ▼           ▼│
│                        ┌─────────┐        ┌─────────┐  ┌────┐│
│                        │ 成功    │        │ Timeout │  │Sig ││
│                        └─────────┘        └─────────┘  └────┘│
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Token 溢出处理

### 5.1 上下文窗口超限

```rust
#[error("Codex ran out of room in the model's context window...")]
ContextWindowExceeded,
```

- 明确标记为**不可重试**错误
- 需要用户开启新线程或清除历史

### 5.2 截断策略

```rust
// TruncationPolicy 用于控制输出截断
pub enum TruncationPolicy {
    Bytes(usize),
    Lines(usize),
}

pub fn truncate_text(text: &str, policy: TruncationPolicy) -> String {
    match policy {
        TruncationPolicy::Bytes(max_bytes) => { /* ... */ }
        TruncationPolicy::Lines(max_lines) => { /* ... */ }
    }
}
```

---

## 6. 配额与限流处理

### 6.1 配额耗尽错误

```rust
#[error("Quota exceeded. Check your plan and billing details.")]
QuotaExceeded,  // 不可重试

#[error("Selected model is at capacity. Please try a different model.")]
ServerOverloaded,  // 不可重试
```

### 6.2 使用限制错误详情

```rust
#[derive(Debug)]
pub struct UsageLimitReachedError {
    pub(crate) plan_type: Option<PlanType>,
    pub(crate) resets_at: Option<DateTime<Utc>>,
    pub(crate) rate_limits: Option<Box<RateLimitSnapshot>>,
    pub(crate) promo_message: Option<String>,
}
```

根据用户套餐类型(Free/Plus/Pro/Team等)显示不同的引导消息。

---

## 7. 错误消息格式化

### 7.1 UI 错误消息截断

```rust
const ERROR_MESSAGE_UI_MAX_BYTES: usize = 2 * 1024; // 2 KiB

pub fn get_error_message_ui(e: &CodexErr) -> String {
    let message = match e {
        CodexErr::Sandbox(SandboxErr::Denied { output, .. }) => {
            // 优先使用 aggregated_output，其次 stderr，最后 stdout
            let aggregated = output.aggregated_output.text.trim();
            if !aggregated.is_empty() {
                output.aggregated_output.text.clone()
            } else {
                // 组合 stderr 和 stdout
                match (stderr.is_empty(), stdout.is_empty()) {
                    (false, false) => format!("{stderr}\n{stdout}"),
                    (false, true) => output.stderr.text.clone(),
                    (true, false) => output.stdout.text.clone(),
                    (true, true) => format!("command failed with exit code {}", output.exit_code),
                }
            }
        }
        CodexErr::Sandbox(SandboxErr::Timeout { output }) => {
            format!("error: command timed out after {} ms", output.duration.as_millis())
        }
        _ => e.to_string(),
    };

    truncate_text(&message, TruncationPolicy::Bytes(ERROR_MESSAGE_UI_MAX_BYTES))
}
```

---

## 8. 协议层错误映射

### 8.1 to_codex_protocol_error()

将内部错误映射到客户端协议错误：

```rust
impl CodexErr {
    pub fn to_codex_protocol_error(&self) -> CodexErrorInfo {
        match self {
            CodexErr::ContextWindowExceeded => CodexErrorInfo::ContextWindowExceeded,
            CodexErr::UsageLimitReached(_)
            | CodexErr::QuotaExceeded
            | CodexErr::UsageNotIncluded => CodexErrorInfo::UsageLimitExceeded,
            CodexErr::ServerOverloaded => CodexErrorInfo::ServerOverloaded,
            CodexErr::RetryLimit(_) => CodexErrorInfo::ResponseTooManyFailedAttempts { ... },
            CodexErr::ConnectionFailed(_) => CodexErrorInfo::HttpConnectionFailed { ... },
            CodexErr::ResponseStreamFailed(_) => CodexErrorInfo::ResponseStreamConnectionFailed { ... },
            CodexErr::Sandbox(_) => CodexErrorInfo::SandboxError,
            _ => CodexErrorInfo::Other,
        }
    }
}
```

---

## 9. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `codex/codex-rs/core/src/error.rs` | `CodexErr`主错误枚举，`is_retryable()`判定，`to_codex_protocol_error()`映射 |
| `codex/codex-rs/core/src/tools/sandboxing.rs` | `ExecApprovalRequirement`三档审批策略，`ApprovalStore`缓存 |
| `codex/codex-rs/core/src/exec.rs` | `ExecExpiration`超时抽象，`DEFAULT_EXEC_COMMAND_TIMEOUT_MS`常量 |
| `codex/codex-rs/core/src/model_provider_info.rs` | `max_stream_retries`/`max_request_retries`重试配置 |
| `codex/codex-rs/core/src/sandboxing/` | Landlock+Seccomp 沙箱实现 |

---

## 10. 设计亮点与启示

### 10.1 Rust 类型安全的优势

1. **穷尽匹配检查**: `is_retryable()` 使用 `match`，编译器确保所有错误类型都被处理
2. **零成本抽象**: `SandboxErr` 嵌套不会带来运行时开销
3. **错误转换链**: `#[from]` 属性自动实现错误转换

### 10.2 显式优于隐式

- 可重试判定采用**白名单**而非黑名单，避免遗漏
- 审批策略三档明确，无歧义
- 所有错误变体都有清晰的文档注释

### 10.3 安全优先设计

- `Sandbox` 错误整体不可重试，防止绕过
- `QuotaExceeded` 明确不可重试，避免无效请求
- 三档审批策略将安全决策权交给用户

---

*文档版本: 2026-02-21*
*基于代码版本: codex-rs (baseline 2026-02-08)*
