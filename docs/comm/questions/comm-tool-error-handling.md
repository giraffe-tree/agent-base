# 5 大 AI Coding Agent 工具调用错误处理机制对比分析

**结论先行**: 5 大 AI Coding Agent 在工具调用错误处理方面呈现出**"分层防御"**的共性架构，但在**恢复策略**上差异显著：Gemini CLI 采用 **Final Warning Turn 优雅恢复**，Kimi CLI 依赖 **Checkpoint + D-Mail 时间旅行**，SWE-agent 使用 **Autosubmit 自动提交**，OpenCode 实现 **Compaction + Doom Loop 检测**，Codex 则专注 **降级重试 + 三档审批**的安全模型。

---

## 1. 工具调用错误处理总览

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                        工具调用错误处理通用架构                                        │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │  错误检测    │───▶│  错误分类    │───▶│  恢复策略    │───▶│  结果反馈    │          │
│  │  Detection  │    │Classification│    │  Recovery   │    │   Feedback   │          │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘          │
│         │                  │                  │                  │                 │
│         ▼                  ▼                  ▼                  ▼                 │
│   ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐              │
│   │参数校验  │      │可重试    │      │指数退避  │      │结构化    │              │
│   │超时检测  │      │vs 致命   │      │自动恢复  │      │错误返回  │              │
│   │沙箱拒绝  │      │          │      │用户介入  │      │日志记录  │              │
│   └──────────┘      └──────────┘      └──────────┘      └──────────┘              │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心维度对比（5个）

### 2.1 工具调用失败重试机制

| 项目 | 重试库/机制 | 最大重试次数 | 退避策略 | 特殊特性 |
|------|------------|-------------|---------|---------|
| **Codex** | 自定义实现 | stream: 5, request: 4 | 指数退避 + 抖动 | WebSocket失败降级到HTTPS |
| **Gemini CLI** | 自定义retry.ts | 3次 | 指数退避 + 抖动 | 429特殊处理，模型降级 |
| **Kimi CLI** | tenacity库 | 3次 (max_retries_per_step) | wait_exponential_jitter | 仅重试网络/API错误 |
| **OpenCode** | 自定义retry.ts | 无明确上限 | 指数退避 + 响应头支持 | resetTimeoutOnProgress |
| **SWE-agent** | 自定义实现 | 3次 (max_requeries) | 指数退避 | 错误分类处理，autosubmit |

**关键差异分析**:
- **Codex**: 区分 stream 和 request 两种重试场景，`is_retryable()` 方法显式枚举可重试错误类型
- **Gemini CLI**: 智能识别 429 配额错误，区分 TerminalQuotaError vs RetryableQuotaError
- **OpenCode**: 支持 `Retry-After` 响应头三种格式（毫秒/秒/HTTP日期），`resetTimeoutOnProgress` 创新机制
- **Kimi CLI**: 依赖 Python tenacity 库，仅重试网络/API层错误，业务错误不重试
- **SWE-agent**: `max_requeries=3` 限制格式错误重试，`forward_with_handling()` 模板化错误反馈

### 2.2 最大轮次/迭代限制

| 项目 | 配置位置 | 默认值 | 实现方式 | 达到限制后行为 |
|------|---------|-------|---------|---------------|
| **Codex** | model_provider_info.rs | stream: 5, request: 4 | 重试计数器 | 返回RetryLimit错误 |
| **Gemini CLI** | client.ts/types.ts | 全局: 100, Agent: 15 | checkTermination() | 执行Final Warning Turn恢复 |
| **Kimi CLI** | config.py | 100 (max_steps_per_turn) | while循环检查 | 抛出MaxStepsReached异常 |
| **OpenCode** | agent.ts/prompt.ts | Infinity (未设置时) | steps参数检查 | 注入MAX_STEPS提示，禁用工具 |
| **SWE-agent** | models.py | 0 (不限制) | per_instance_call_limit | 触发autosubmit |

### 2.3 工具调用超时处理

| 项目 | 默认超时 | 配置方式 | 特殊机制 |
|------|---------|---------|---------|
| **Codex** | 10秒 (exec) | ExecExpiration枚举 | CancellationToken支持 |
| **Gemini CLI** | 5分钟 (agent) | DeadlineTimer类 | 可暂停/恢复，动态扩展 |
| **Kimi CLI** | 60秒 (MCP) | mcp.client.tool_call_timeout_ms | MCP工具单独配置 |
| **OpenCode** | 30秒 (MCP), 2分钟 (bash) | timeout参数 | resetTimeoutOnProgress |
| **SWE-agent** | 30秒 (执行), 1800秒 (总) | ToolConfig | 连续超时计数，3次后退出 |

### 2.4 Token溢出导致工具调用受限

| 项目 | 检测方式 | 处理策略 | 对工具调用的影响 |
|------|---------|---------|-----------------|
| **Codex** | estimate_token_count | TruncationPolicy截断 | 工具结果可能被截断 |
| **Gemini CLI** | ContextWindowWillOverflow事件 | chatCompressionService压缩 | 触发/compaction工具 |
| **Kimi CLI** | token_count检查 | SimpleCompaction压缩 | 创建checkpoint，继续工具调用 |
| **OpenCode** | isOverflow检测 | Prune + Compaction | 工具调用上下文被压缩 |
| **SWE-agent** | max_observation_length | 输出截断 | 工具返回结果被截断 |

### 2.5 工具调用权限/确认介入

| 项目 | 介入触发条件 | 介入方式 | 特殊机制 |
|------|------------|---------|---------|
| **Codex** | 危险工具调用、沙箱拒绝 | 实时通知 | AskForApproval策略(Skip/NeedsApproval/Forbidden) |
| **Gemini CLI** | 危险工具Kind、达到限制 | 确认循环 | PolicyDecision.ASK_USER |
| **Kimi CLI** | 危险命令列表 | Approval.request | 审批管道 |
| **OpenCode** | 权限模式匹配 | PermissionNext.ask | 区分Rejected/Corrected/Denied |
| **SWE-agent** | 无（纯自动化） | - | 依赖Docker沙箱 |

---

## 3. 扩展维度对比（6个）

### 3.1 工具参数验证错误

| 项目 | 错误类型 | 验证层级 | 恢复策略 |
|------|---------|---------|---------|
| **Codex** | JSON解析错误、Schema校验 | tool输入层 | 返回错误给LLM自纠正 |
| **Gemini CLI** | INVALID_TOOL_PARAMS | ToolWrapper.execute | LLM重试，参数调整 |
| **Kimi CLI** | ToolParseError, ToolValidateError | JSON解析+Schema校验 | 自动包装为ToolError |
| **OpenCode** | Zod Schema校验 | 工具定义层 | 返回validation error |
| **SWE-agent** | FormatError, FunctionCallingFormatError | Agent解析层 | 模板化错误反馈重试 |

### 3.2 工具未找到错误

| 项目 | 错误类型 | 检测位置 | 处理方式 |
|------|---------|---------|---------|
| **Codex** | Tool not available | ToolRegistry查询 | 返回Unavailable错误 |
| **Gemini CLI** | TOOL_NOT_REGISTERED | ToolWrapper初始化 | Fatal error，LLM调整 |
| **Kimi CLI** | ToolNotFoundError | 工具路由层 | ToolError包装 |
| **OpenCode** | MCP工具获取失败 | MCP客户端 | 返回error给模型 |
| **SWE-agent** | UnknownCommand/UnknownAction | Command解析 | 重试+autosubmit |

### 3.3 配额与速率限制

| 项目 | 错误类型 | 检测方式 | 重试策略 |
|------|---------|---------|---------|
| **Codex** | QuotaExceeded, ServerOverloaded | HTTP状态码+错误码 | is_retryable()判断 |
| **Gemini CLI** | TerminalQuotaError, RetryableQuotaError | googleQuotaErrors.ts | 区分终端/可重试配额 |
| **Kimi CLI** | rate_limit错误 | API响应 | 指数退避重试 |
| **OpenCode** | Rate limit, FreeUsageLimit | 响应头+模式匹配 | 读取Retry-After头 |
| **SWE-agent** | CostLimitExceeded | 成本追踪器 | 触发autosubmit退出 |

### 3.4 格式与解析错误

| 项目 | 错误类型 | 触发场景 | 处理机制 |
|------|---------|---------|---------|
| **Codex** | JSON解析失败 | 工具参数解析 | 返回CodexErr::InvalidRequest |
| **Gemini CLI** | 内部处理 | 工具响应解析 | 错误包装为ToolError |
| **Kimi CLI** | ToolParseError | JSON解析失败 | 四层错误体系 |
| **OpenCode** | Doom loop检测 | 重复工具失败 | 检测最后3次调用模式 |
| **SWE-agent** | FormatError, FunctionCallingFormatError | 响应解析失败 | 模板化重试(max_requeries=3) |

### 3.5 文件系统相关错误

| 项目 | 错误类型 | 分类策略 | 恢复方式 |
|------|---------|---------|---------|
| **Codex** | SandboxErr::Denied | Landlock沙箱 | 网络策略决策 |
| **Gemini CLI** | FILE_NOT_FOUND, PERMISSION_DENIED, PATH_NOT_IN_WORKSPACE | ToolErrorType枚举 | LLM自纠正 |
| **Kimi CLI** | 底层Python异常 | 工具执行层 | 转换为ToolRuntimeError |
| **OpenCode** | 文件工具错误 | fs工具实现 | 返回error结果 |
| **SWE-agent** | 运行时异常 | subprocess执行 | forward_with_handling处理 |

### 3.6 沙箱与执行错误

| 项目 | 错误类型 | 沙箱机制 | 错误恢复 |
|------|---------|---------|---------|
| **Codex** | SandboxErr(Denied, Timeout, Signal, LandlockRestrict) | Landlock+Seccomp | 三档审批策略 |
| **Gemini CLI** | EXECUTION_FAILED, SHELL_EXECUTE_ERROR | 受限执行环境 | 错误状态传递 |
| **Kimi CLI** | ToolRuntimeError | 受限shell | checkpoint回滚 |
| **OpenCode** | bash工具exit code | 无原生沙箱 | 错误返回给模型 |
| **SWE-agent** | CommandTimeoutError, SwerexException | Docker沙箱 | autosubmit/重试 |

---

## 4. 错误分类体系对比

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                           错误分类体系层级对比                                         │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  Codex (Rust)                      Gemini CLI (TypeScript)                          │
│  ┌──────────────────┐              ┌──────────────────────┐                         │
│  │   CodexErr       │              │   Error (base)       │                         │
│  │   (主错误枚举)    │              │   └─ ToolError       │                         │
│  ├──────────────────┤              │      (15+ types)     │                         │
│  │ • SandboxErr     │              ├──────────────────────┤                         │
│  │ • Stream         │              │ • POLICY_VIOLATION   │                         │
│  │ • Timeout        │              │ • INVALID_TOOL_PARAMS│                         │
│  │ • QuotaExceeded  │              │ • PATH_NOT_IN_WORKSPACE│                       │
│  │ • ...            │              │ • FILE_NOT_FOUND     │                         │
│  └──────────────────┘              └──────────────────────┘                         │
│                                                                                     │
│  Kimi CLI (Python)                 SWE-agent (Python)                               │
│  ┌──────────────────┐              ┌──────────────────────┐                         │
│  │ ToolReturnValue  │              │ Exception (base)     │                         │
│  │ ├─ ToolOk        │              ├──────────────────────┤                         │
│  │ └─ ToolError ◄───┼── 四层继承 ──┤ • FormatError        │                         │
│  │    ├─ ToolNotFound│             │   └─ FunctionCalling │                         │
│  │    ├─ ToolParse  │             │ • ContextWindow      │                         │
│  │    ├─ ToolValidate│            │ • CostLimitExceeded  │                         │
│  │    └─ ToolRuntime│             │ • CommandTimeout     │                         │
│  └──────────────────┘              └──────────────────────┘                         │
│                                                                                     │
│  OpenCode (TypeScript)                                                              │
│  ┌──────────────────────────────────────┐                                          │
│  │ Provider-specific error parsing      │                                          │
│  │ • APIError with retryable flag       │                                          │
│  │ • ContextOverflowError               │                                          │
│  │ • Doom loop detection (behavioral)   │                                          │
│  └──────────────────────────────────────┘                                          │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 可重试错误判定策略对比

| 项目 | 判定方式 | 可重试错误示例 | 不可重试错误示例 |
|------|---------|---------------|-----------------|
| **Codex** | `is_retryable()` 显式枚举 | Stream, Timeout, ConnectionFailed | QuotaExceeded, Sandbox, ContextWindowExceeded |
| **Gemini CLI** | `classifyFailureKind()` | transient类别错误 | terminal类别错误 |
| **OpenCode** | `retryable()` 函数 | APIError.data.isRetryable=true | ContextOverflowError |
| **SWE-agent** | 异常类型 + `max_requeries` | FormatError(3次内) | CostLimitExceeded |
| **Kimi CLI** | 错误类型判断 | 网络/API错误 | ToolParseError, ToolValidateError |

---

## 6. 关键设计差异总结

### 6.1 工具调用恢复策略对比

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                           恢复策略差异                                               │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│   Gemini CLI              Kimi CLI              SWE-agent        OpenCode           │
│   ┌─────────────┐         ┌─────────────┐       ┌─────────────┐  ┌─────────────┐    │
│   │ 达到限制    │         │ 达到限制    │       │ 达到限制    │  │ 达到限制    │    │
│   └──────┬──────┘         └──────┬──────┘       └──────┬──────┘  └──────┬──────┘    │
│          ▼                       ▼                    ▼             ▼               │
│   ┌─────────────┐         ┌─────────────┐       ┌─────────────┐  ┌─────────────┐    │
│   │Final Warning│         │ Checkpoint  │       │ Autosubmit  │  │  Compaction │    │
│   │Turn 恢复    │         │ + D-Mail    │       │ 自动提交    │  │ + Prune     │    │
│   │             │         │ 时间旅行    │       │ 当前patch   │  │             │    │
│   └──────┬──────┘         └──────┬──────┘       └──────┬──────┘  └──────┬──────┘    │
│          ▼                       ▼                    ▼             ▼               │
│   继续对话上下文          回滚到保存点           结束并提交      继续精简上下文        │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 沙箱安全模型对比

| 项目 | 沙箱机制 | 错误处理方式 | 用户介入级别 |
|------|---------|-------------|-------------|
| **Codex** | Landlock + Seccomp | 三档审批(Skip/NeedsApproval/Forbidden) | 实时通知 |
| **Gemini CLI** | 受限执行环境 | POLICY_VIOLATION错误类型 | 确认循环 |
| **Kimi CLI** | 受限shell | Checkpoint联动回滚 | 审批管道 |
| **OpenCode** | 无原生沙箱 | 依赖外部权限系统 | 权限模式匹配 |
| **SWE-agent** | Docker沙箱 | Blocklist过滤 | 无介入（纯自动化） |

### 6.3 超时机制创新对比

| 项目 | 核心创新 | 实现文件 | 适用场景 |
|------|---------|---------|---------|
| **OpenCode** | `resetTimeoutOnProgress` | `session/retry.ts` | 长任务执行 |
| **Gemini CLI** | `DeadlineTimer`可暂停 | `utils/deadlineTimer.ts` | 用户交互暂停 |
| **SWE-agent** | 连续超时计数器(3次exit) | `agent/agents.py` | 防止无限挂起 |
| **Kimi CLI** | MCP单独配置 + Checkpoint回滚 | `tooling/` | 工具级超时 |
| **Codex** | `ExecExpiration`统一抽象 | `exec/mod.rs` | 命令执行超时 |

---

## 7. 选型建议矩阵

### 7.1 按场景选择参考

| 场景需求 | 推荐项目 | 原因 |
|---------|---------|------|
| 企业级安全沙箱 | Codex | Landlock+Seccomp+三档审批 |
| 复杂状态恢复 | Kimi CLI | Checkpoint+D-Mail时间旅行 |
| 纯自动化CI/CD | SWE-agent | Autosubmit+Docker隔离 |
| 长任务执行 | OpenCode | resetTimeoutOnProgress |
| 智能配额管理 | Gemini CLI | Terminal/Retryable配额区分 |

### 7.2 按错误类型设计参考

| 错误类型 | 最佳实践参考 |
|---------|-------------|
| 参数验证错误 | Kimi CLI 四层错误继承体系 |
| 配额限流错误 | Gemini CLI 智能分类策略 |
| 格式解析错误 | SWE-agent Jinja2模板化反馈 |
| 沙箱安全错误 | Codex 三档审批策略 |
| 重复失败检测 | OpenCode Doom loop检测 |

---

## 8. 核心源码文件索引

### Codex (Rust)
- `codex/codex-rs/core/src/error.rs` - CodexErr主错误枚举，`is_retryable()`判定
- `codex/codex-rs/core/src/tools/sandboxing.rs` - SandboxErr定义
- `codex/codex-rs/core/src/model_provider_info.rs` - 重试配置

### Gemini CLI (TypeScript)
- `gemini-cli/packages/core/src/tools/tool-error.ts` - ToolErrorType枚举(15+类型)
- `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` - 配额错误分类
- `gemini-cli/packages/core/src/availability/errorClassification.ts` - 错误分类

### Kimi CLI (Python)
- `kimi-cli/packages/kosong/src/kosong/tooling/error.py` - ToolError四层体系
- `kimi-cli/packages/kosong/src/kosong/tooling/__init__.py` - ToolReturnValue

### OpenCode (TypeScript)
- `opencode/packages/opencode/src/session/retry.ts` - 重试逻辑，`retryable()`判定
- `opencode/packages/opencode/src/provider/error.ts` - 错误解析

### SWE-agent (Python)
- `SWE-agent/sweagent/exceptions.py` - 异常层级定义
- `SWE-agent/sweagent/agent/agents.py` - `forward_with_handling()`错误处理

---

## 9. 结论与启示

### 9.1 共性模式

1. **分层错误处理**: 所有项目都实现了检测→分类→恢复→反馈的流水线
2. **可重试判定**: 均显式区分可重试(transient)与不可重试(terminal)错误
3. **指数退避**: 除SWE-agent外均实现了指数退避+抖动的重试策略
4. **LLM反馈**: 错误最终都转换为LLM可理解的结构化消息

### 9.2 差异化创新

| 创新点 | 项目 | 价值 |
|-------|------|------|
| Checkpoint时间旅行 | Kimi CLI | 状态恢复能力 |
| Autosubmit | SWE-agent | 自动化完成率 |
| Doom loop检测 | OpenCode | 避免重复失败 |
| 三档审批 | Codex | 安全与便利平衡 |
| Final Warning Turn | Gemini CLI | 优雅降级 |

### 9.3 设计启示

1. **恢复策略 > 错误类型**: 不同项目的核心差异在于恢复策略，而非错误分类本身
2. **超时即特征**: 超时处理是区分长任务与交互式Agent的关键设计点
3. **沙箱即边界**: 沙箱错误处理反映了项目的安全模型定位
4. **配额即成本**: 配额错误处理体现了项目的成本控制能力

---

*文档版本: 2026-02-21*
*分析范围: Codex, Gemini CLI, Kimi CLI, OpenCode, SWE-agent*
