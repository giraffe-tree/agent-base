# Codex 如何避免 Tool 无限循环调用

**结论先行**: Codex 通过**硬性重试次数上限** + **三档审批策略** + **工具调用结果去重**三层机制防止 tool 无限循环。核心设计是"相信 LLM 但限制其行为"，通过显式的 `is_retryable()` 白名单和 `AskForApproval` 策略在关键点进行人工介入。

---

## 1. 核心防护机制

### 1.1 重试次数硬性上限

位于 `codex/codex-rs/core/src/model_provider_info.rs`：

```rust
pub struct ModelProviderInfo {
    /// SSE 流断开后的最大重试次数
    pub max_stream_retries: u32,  // 默认: 5

    /// HTTP 请求失败后的最大重试次数
    pub max_request_retries: u32,  // 默认: 4
}
```

**关键设计**: Codex 区分两种重试场景：
- **Stream 重试**: SSE 流断开时的重试（5次）
- **Request 重试**: HTTP 请求失败时的重试（4次）

### 1.2 is_retryable() 显式白名单

位于 `codex/codex-rs/core/src/error.rs:195`：

```rust
impl CodexErr {
    pub fn is_retryable(&self) -> bool {
        match self {
            // 明确不可重试（包括工具相关错误）
            CodexErr::TurnAborted
            | CodexErr::Interrupted
            | CodexErr::QuotaExceeded
            | CodexErr::Sandbox(_)           // ← 沙箱错误不重试
            | CodexErr::RetryLimit(_)        // ← 已达重试上限
            | CodexErr::ContextWindowExceeded
            | CodexErr::UsageLimitReached(_) => false,

            // 可重试的网络/IO错误
            CodexErr::Stream(..)
            | CodexErr::Timeout
            | CodexErr::UnexpectedStatus(_)
            | CodexErr::ConnectionFailed(_) => true,
            // ...
        }
    }
}
```

**防循环关键**: `SandboxErr` 被明确标记为**不可重试**，这意味着工具调用一旦因沙箱策略失败，不会自动重试，必须通过审批流程。

---

## 2. 三档审批策略（AskForApproval）

### 2.1 审批要求分级

位于 `codex/codex-rs/core/src/tools/sandboxing.rs:120`：

```rust
pub(crate) enum ExecApprovalRequirement {
    /// 无需审批，直接执行
    Skip {
        bypass_sandbox: bool,
        proposed_execpolicy_amendment: Option<ExecPolicyAmendment>,
    },

    /// 需要用户审批
    NeedsApproval {
        reason: Option<String>,
        proposed_execpolicy_amendment: Option<ExecPolicyAmendment>,
    },

    /// 禁止执行
    Forbidden { reason: String },
}
```

### 2.2 审批策略生成逻辑

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

**防循环机制**:
- `OnFailure` 模式下，工具调用失败后才需要审批
- 危险工具默认触发 `NeedsApproval`，强制人工介入
- `Forbidden` 直接阻止某些可能产生循环的危险操作

---

## 3. 工具调用结果缓存

### 3.1 ApprovalStore 缓存机制

位于 `codex/codex-rs/core/src/tools/sandboxing.rs:31`：

```rust
#[derive(Clone, Default, Debug)]
pub(crate) struct ApprovalStore {
    // 缓存已审批的请求
    map: HashMap<String, ReviewDecision>,
}

impl ApprovalStore {
    pub fn get<K>(&self, key: &K) -> Option<ReviewDecision>
    where K: Serialize,
    {
        let s = serde_json::to_string(key).ok()?;
        self.map.get(&s).cloned()
    }
}
```

**防循环作用**: 对于完全相同的工具调用请求，Codex 会复用之前的审批决策，避免 LLM 因重复请求相同工具而产生循环。

---

## 4. 超时机制

### 4.1 ExecExpiration 统一抽象

位于 `codex/codex-rs/core/src/exec.rs:79`：

```rust
pub enum ExecExpiration {
    Timeout(Duration),           // 固定超时
    DefaultTimeout,              // 默认10秒
    Cancellation(CancellationToken),  // 可取消
}
```

### 4.2 默认超时配置

```rust
pub const DEFAULT_EXEC_COMMAND_TIMEOUT_MS: u64 = 10_000; // 10秒
```

**防循环作用**: 即使 LLM 陷入循环调用耗时工具，10秒超时也会强制终止执行。

---

## 5. 防循环流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Codex Tool 调用防循环流程                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   LLM 输出 tool call                                             │
│        │                                                        │
│        ▼                                                        │
│   ┌───────────────────┐                                        │
│   │ 检查 ApprovalStore │──已批准──▶ 跳过审批，直接执行           │
│   │ (相同请求缓存)     │                                        │
│   └─────────┬─────────┘                                        │
│             │未批准                                              │
│             ▼                                                   │
│   ┌───────────────────┐                                        │
│   │ ExecApprovalReq   │                                        │
│   │ 策略检查           │                                        │
│   └─────────┬─────────┘                                        │
│             │                                                   │
│     ┌───────┼───────┬──────────┐                               │
│     ▼       ▼       ▼          ▼                               │
│   Skip   Needs   Forbidden   OnFailure                         │
│    │    Approval    │            │                              │
│    │      │         │            │                              │
│    ▼      ▼         ▼            ▼                              │
│  直接   用户确认   拒绝执行    先执行后检查                        │
│  执行    (人工介入)              │                               │
│                                 │                               │
│        ┌────────────────────────┘                               │
│        ▼                                                        │
│   ┌─────────────┐                                              │
│   │  工具执行    │                                              │
│   └──────┬──────┘                                              │
│          │                                                      │
│    ┌─────┴─────┬──────────┐                                    │
│    ▼           ▼          ▼                                    │
│  成功       失败(可重试)  失败(不可重试)                          │
│    │           │           │                                    │
│    │      ┌────┘           └────┐                               │
│    │      ▼                     ▼                               │
│    │  重试计数 < 5?          Sandbox/Quota                      │
│    │      │                     │                               │
│    │     是│                   否│                               │
│    │      ▼                     ▼                               │
│    │  指数退避重试           返回错误                             │
│    │      │                     │ (is_retryable=false)          │
│    └──────┴─────────────────────┘                               │
│                                 │                               │
│                                 ▼                               │
│                          LLM 收到错误                           │
│                          由模型决定下一步                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 与其他 Agent 的对比

| 防护层 | Codex | Gemini CLI | Kimi CLI | OpenCode | SWE-agent |
|--------|-------|------------|----------|----------|-----------|
| **重试上限** | ✅ 5/4次 | ✅ 3次 | ✅ 3次 | ❌ 无明确上限 | ✅ 3次 |
| **循环检测** | ❌ 无 | ✅ LLM-based | ❌ 无 | ✅ Doom loop | ❌ 无 |
| **状态回滚** | ❌ 无 | ❌ 无 | ✅ Checkpoint | ❌ 无 | ❌ 无 |
| **审批介入** | ✅ 三档策略 | ✅ 策略驱动 | ✅ 危险命令 | ✅ 权限规则 | ❌ 无 |
| **自动退出** | ❌ 无 | ✅ Final Warning | ❌ 无 | ❌ 无 | ✅ Autosubmit |

---

## 7. 总结

Codex 的防循环设计哲学是**"限制 + 人工介入"**：

1. **硬性限制**: 通过 `max_stream_retries` 和 `max_request_retries` 限制重试次数
2. **白名单机制**: `is_retryable()` 显式指定哪些错误可重试，工具错误默认不重试
3. **审批卡点**: `AskForApproval` 策略在危险工具调用前强制人工确认
4. **超时兜底**: 10秒默认超时防止耗时工具循环

Codex 不依赖智能循环检测，而是通过**严格的策略和人工介入**来防止循环，这符合其企业级安全定位。

---

*文档版本: 2026-02-21*
*基于代码版本: codex-rs (baseline 2026-02-08)*
