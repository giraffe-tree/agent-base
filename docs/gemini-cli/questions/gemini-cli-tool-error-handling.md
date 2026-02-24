# Gemini CLI 工具调用错误处理机制

## TL;DR（结论先行）

一句话定义：Gemini CLI 采用 **Scheduler 状态机 + Final Warning Turn** 的优雅恢复架构，通过 `ToolErrorType` 枚举定义 15+ 种错误类型，并创新性地将配额错误细分为 `TerminalQuotaError`(日限额) 与 `RetryableQuotaError`(分钟限额)，实现了智能的错误分类与恢复策略。

Gemini CLI 的核心取舍：**细粒度错误分类 + 优雅降级**（对比其他项目的简单重试或直接报错）

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

没有完善的错误处理机制时：
- 用户问"修复这个 bug" → LLM 尝试读取文件 → 文件不存在 → 直接报错退出
- 用户问"分析代码" → 达到 API 配额 → 无意义地重试直到超时
- 用户问"运行测试" → 工具调用陷入循环 → 无限循环消耗资源

有了完善的错误处理：
- 文件不存在 → 返回错误给 LLM → LLM 自纠正（询问正确路径）
- 达到配额 → 智能区分日限额/分钟限额 → 日限额切换模型，分钟限额延迟重试
- 循环检测 → 检测到重复模式 → 主动终止并提示用户

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 错误类型繁杂 | 无法针对性恢复，一刀切处理导致用户体验差 |
| 配额限制复杂 | 无意义重试浪费资源，或直接放弃可用机会 |
| 工具调用循环 | 无限循环消耗 token 和 API 配额 |
| 上下文溢出 | 长对话导致 token 超限，会话被迫中断 |

---

## 2. 整体架构

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Scheduler                                       │
│ gemini-cli/packages/core/src/core/client.ts                  │
└───────────────────────┬─────────────────────────────────────┘
                        │ 工具调用请求
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ 工具错误处理系统 ▓▓▓                                      │
│                                                              │
│ ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐ │
│ │ ToolErrorType   │  │ 配额错误分类     │  │ 重试机制      │ │
│ │ 15+ 错误类型    │  │ Terminal/       │  │ 指数退避      │ │
│ │ 定义            │  │ Retryable       │  │ + 抖动        │ │
│ └────────┬────────┘  └────────┬────────┘  └───────┬───────┘ │
│          │                    │                    │         │
│          └────────────────────┼────────────────────┘         │
│                               ▼                              │
│                    ┌─────────────────────┐                   │
│                    │   错误恢复策略       │                   │
│                    │ - Final Warning Turn│                   │
│                    │ - 上下文压缩        │                   │
│                    │ - 循环检测          │                   │
│                    └─────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `ToolErrorType` | 定义 15+ 种工具错误类型枚举 | `gemini-cli/packages/core/src/tools/tool-error.ts` |
| `isFatalToolError()` | 判定错误是否为致命错误（仅磁盘空间不足） | `gemini-cli/packages/core/src/tools/tool-error.ts` |
| `TerminalQuotaError` | 日限额等不可重试配额错误 | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` |
| `RetryableQuotaError` | 分钟限额等可重试配额错误 | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` |
| `classifyGoogleError()` | 智能分类 Google API 错误 | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` |
| `retryWithBackoff()` | 带指数退避和抖动的重试逻辑 | `gemini-cli/packages/core/src/utils/retry.ts` |
| `handleFinalWarningTurn()` | 最大轮次限制时的优雅恢复 | `gemini-cli/packages/core/src/core/client.ts` |

### 2.3 配额错误分类流程

```mermaid
flowchart TD
    A[Google API 错误] --> B{HTTP 状态码}

    B -->|404| C[ModelNotFoundError]
    B -->|429/403| D[检查 QuotaFailure]
    B -->|500| E[ServerError]

    D --> F{quotaId 包含}
    F -->|PerDay/Daily| G[TerminalQuotaError<br/>日限额-不可重试]
    F -->|PerMinute| H[RetryableQuotaError<br/>分钟限额-可重试60s]

    D --> I{Cloud Code API}
    I -->|RATE_LIMIT_EXCEEDED| H
    I -->|QUOTA_EXHAUSTED| G

    C --> J[返回分类错误]
    E --> J
    G --> J
    H --> J

    style G fill:#FF6B6B
    style H fill:#90EE90
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | 接收 Google API 错误 | 统一错误入口，便于集中处理 |
| 2 | 解析 HTTP 状态码 | 快速分流，404/500 直接处理 |
| 3 | 检查 QuotaFailure 详情 | 细粒度分析配额错误类型 |
| 4 | 区分日限额/分钟限额 | 日限额不可重试，分钟限额可延迟重试 |
| 5 | 返回分类后的错误对象 | 上层根据错误类型选择恢复策略 |

---

## 3. 核心组件详细分析

### 3.1 ToolErrorType 错误类型体系

#### 职责定位

定义工具执行过程中可能遇到的所有错误类型，为错误处理和恢复提供类型基础。

#### 错误分类结构

```text
┌─────────────────────────────────────────────────────────────┐
│ ToolErrorType 枚举                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  安全与策略                                                  │
│  └── POLICY_VIOLATION        # 内容策略违规                  │
│                                                             │
│  通用错误                                                    │
│  ├── INVALID_TOOL_PARAMS     # 参数验证失败                  │
│  ├── UNKNOWN                 # 未知错误                      │
│  ├── UNHANDLED_EXCEPTION     # 未处理异常                    │
│  ├── TOOL_NOT_REGISTERED     # 工具未注册                    │
│  └── EXECUTION_FAILED        # 执行失败                      │
│                                                             │
│  文件系统错误 (10+ 类型)                                      │
│  ├── FILE_NOT_FOUND          # 文件不存在                    │
│  ├── FILE_WRITE_FAILURE      # 写入失败                      │
│  ├── READ_CONTENT_FAILURE    # 读取失败                      │
│  ├── PERMISSION_DENIED       # 权限不足                      │
│  ├── NO_SPACE_LEFT           # 磁盘空间不足（致命）           │
│  ├── PATH_NOT_IN_WORKSPACE   # 路径超出工作区                │
│  └── FILE_TOO_LARGE          # 文件过大                      │
│                                                             │
│  Shell/MCP 错误                                              │
│  ├── SHELL_EXECUTE_ERROR     # Shell 执行错误                │
│  └── MCP_TOOL_ERROR          # MCP 工具错误                  │
│                                                             │
│  其他                                                        │
│  └── STOP_EXECUTION          # 停止执行                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 关键代码

```typescript
// gemini-cli/packages/core/src/tools/tool-error.ts
export enum ToolErrorType {
  POLICY_VIOLATION = 'policy_violation',
  INVALID_TOOL_PARAMS = 'invalid_tool_params',
  UNKNOWN = 'unknown',
  UNHANDLED_EXCEPTION = 'unhandled_exception',
  TOOL_NOT_REGISTERED = 'tool_not_registered',
  EXECUTION_FAILED = 'execution_failed',
  FILE_NOT_FOUND = 'file_not_found',
  FILE_WRITE_FAILURE = 'file_write_failure',
  READ_CONTENT_FAILURE = 'read_content_failure',
  PERMISSION_DENIED = 'permission_denied',
  NO_SPACE_LEFT = 'no_space_left',
  PATH_NOT_IN_WORKSPACE = 'path_not_in_workspace',
  FILE_TOO_LARGE = 'file_too_large',
  SHELL_EXECUTE_ERROR = 'shell_execute_error',
  MCP_TOOL_ERROR = 'mcp_tool_error',
  STOP_EXECUTION = 'stop_execution',
}
```

#### 致命错误判定

```typescript
// gemini-cli/packages/core/src/tools/tool-error.ts
export function isFatalToolError(errorType?: string): boolean {
  if (!errorType) return false;

  const fatalErrors = new Set<string>([ToolErrorType.NO_SPACE_LEFT]);
  return fatalErrors.has(errorType);
}
```

**设计要点**：
1. **仅磁盘空间不足为致命错误**：其他错误都允许 LLM 自纠正
2. **细粒度分类**：15+ 种错误类型支持精准的错误报告和恢复建议

---

### 3.2 配额错误智能分类系统

#### 职责定位

将 Google API 的配额错误智能分类为可重试和不可重试两类，指导上层选择合适的恢复策略。

#### 配额错误分类体系

```text
┌─────────────────────────────────────────────────────────────────┐
│                    Gemini CLI 配额错误分类                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   429/403 错误                                                   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────┐                                               │
│   │ classify    │                                               │
│   │ GoogleError │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│    ┌─────┼─────┬─────────────┐                                  │
│    ▼     ▼     ▼             ▼                                  │
│ ┌────┐ ┌────┐ ┌──────────┐ ┌──────────┐                        │
│ │404 │ │500 │ │ Terminal │ │ Retryable│                        │
│ │    │ │    │ │ Quota    │ │ Quota    │                        │
│ └────┘ └────┘ └────┬─────┘ └────┬─────┘                        │
│                    │            │                               │
│                    ▼            ▼                               │
│              ┌─────────┐  ┌──────────┐                         │
│              │ 日限额  │  │ 分钟限额 │                         │
│              │ 不可重试│  │ 可重试   │                         │
│              │ 模型降级│  │ 延迟60s  │                         │
│              └─────────┘  └──────────┘                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 关键代码实现

```typescript
// gemini-cli/packages/core/src/utils/googleQuotaErrors.ts

/**
 * 不可重试错误：硬性配额限制已到达（如日限额）
 */
export class TerminalQuotaError extends Error {
  retryDelayMs?: number;

  constructor(
    message: string,
    override readonly cause: GoogleApiError,
    retryDelaySeconds?: number,
  ) {
    super(message);
    this.name = 'TerminalQuotaError';
    this.retryDelayMs = retryDelaySeconds ? retryDelaySeconds * 1000 : undefined;
  }
}

/**
 * 可重试错误：临时配额问题（如每分钟限制）
 */
export class RetryableQuotaError extends Error {
  retryDelayMs?: number;

  constructor(
    message: string,
    override readonly cause: GoogleApiError,
    retryDelaySeconds?: number,
  ) {
    super(message);
    this.name = 'RetryableQuotaError';
    this.retryDelayMs = retryDelaySeconds ? retryDelaySeconds * 1000 : undefined;
  }
}
```

#### 分类逻辑

```typescript
// gemini-cli/packages/core/src/utils/googleQuotaErrors.ts
export function classifyGoogleError(error: unknown): unknown {
  const googleApiError = parseGoogleApiError(error);
  const status = googleApiError?.code ?? getErrorStatus(error);

  // 404 错误 → ModelNotFoundError
  if (status === 404) {
    return new ModelNotFoundError(message, status);
  }

  // 检查 QuotaFailure 中的日限额
  const quotaFailure = googleApiError.details.find(
    (d): d is QuotaFailure =>
      d['@type'] === 'type.googleapis.com/google.rpc.QuotaFailure',
  );

  if (quotaFailure) {
    for (const violation of quotaFailure.violations) {
      const quotaId = violation.quotaId ?? '';
      // 日限额 = 终端错误
      if (quotaId.includes('PerDay') || quotaId.includes('Daily')) {
        return new TerminalQuotaError(
          `You have exhausted your daily quota on this model.`,
          googleApiError,
        );
      }
    }
  }

  // Cloud Code API 特定处理
  if (errorInfo?.domain?.includes('cloudcode-pa.googleapis.com')) {
    if (errorInfo.reason === 'RATE_LIMIT_EXCEEDED') {
      return new RetryableQuotaError(message, googleApiError, delaySeconds ?? 10);
    }
    if (errorInfo.reason === 'QUOTA_EXHAUSTED') {
      return new TerminalQuotaError(message, googleApiError, delaySeconds);
    }
  }

  // 分钟限额 = 可重试
  if (quotaId.includes('PerMinute')) {
    return new RetryableQuotaError(
      `${googleApiError.message}\nSuggested retry after 60s.`,
      googleApiError,
      60,
    );
  }

  // ...
}
```

**算法要点**：
1. **quotaId 模式匹配**：通过 `PerDay`/`Daily` 识别日限额，`PerMinute` 识别分钟限额
2. **Cloud Code API 特殊处理**：根据 `reason` 字段区分 `RATE_LIMIT_EXCEEDED` 和 `QUOTA_EXHAUSTED`
3. **建议延迟时间**：分钟限额建议 60 秒后重试

---

### 3.3 重试机制

#### 职责定位

提供带指数退避和抖动的重试逻辑，支持根据错误类型动态调整重试策略。

#### 关键代码

```typescript
// gemini-cli/packages/core/src/utils/retry.ts
export const DEFAULT_MAX_ATTEMPTS = 3;

const DEFAULT_RETRY_OPTIONS: RetryOptions = {
  maxAttempts: DEFAULT_MAX_ATTEMPTS,
  initialDelayMs: 5000,    // 5秒初始延迟
  maxDelayMs: 30000,       // 最大30秒
  shouldRetryOnError: isRetryableError,
};

export function isRetryableError(
  error: Error | unknown,
  retryFetchErrors?: boolean,
): boolean {
  // 网络错误代码
  const RETRYABLE_NETWORK_CODES = [
    'ECONNRESET', 'ETIMEDOUT', 'EPIPE', 'ENOTFOUND',
    'EAI_AGAIN', 'ECONNREFUSED',
    // SSL/TLS 瞬态错误
    'ERR_SSL_SSLV3_ALERT_BAD_RECORD_MAC',
    'ERR_SSL_WRONG_VERSION_NUMBER',
  ];

  const errorCode = getNetworkErrorCode(error);
  if (errorCode && RETRYABLE_NETWORK_CODES.includes(errorCode)) {
    return true;
  }

  // ApiError 状态码检查
  if (error instanceof ApiError) {
    if (error.status === 400) return false;  // 明确不重试 400
    return error.status === 429 || (error.status >= 500 && error.status < 600);
  }

  return false;
}
```

#### 带退避的重试逻辑

```typescript
// gemini-cli/packages/core/src/utils/retry.ts
export async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  options?: Partial<RetryOptions>,
): Promise<T> {
  let attempt = 0;
  let currentDelay = initialDelayMs;

  while (attempt < maxAttempts) {
    attempt++;
    try {
      const result = await fn();
      return result;
    } catch (error) {
      const classifiedError = classifyGoogleError(error);

      // TerminalQuotaError 触发模型降级
      if (classifiedError instanceof TerminalQuotaError) {
        if (onPersistent429) {
          const fallbackModel = await onPersistent429(authType, classifiedError);
          if (fallbackModel) {
            attempt = 0;  // 重置尝试次数
            currentDelay = initialDelayMs;
            continue;
          }
        }
        throw classifiedError;
      }

      // RetryableQuotaError 使用建议延迟
      if (classifiedError instanceof RetryableQuotaError) {
        if (classifiedError.retryDelayMs !== undefined) {
          await delay(classifiedError.retryDelayMs, signal);
          continue;
        }
      }

      // 指数退避 + 抖动
      const jitter = currentDelay * 0.3 * (Math.random() * 2 - 1);
      const delayWithJitter = Math.max(0, currentDelay + jitter);
      await delay(delayWithJitter, signal);
      currentDelay = Math.min(maxDelayMs, currentDelay * 2);
    }
  }
}
```

**设计要点**：
1. **指数退避**：延迟时间翻倍，最大 30 秒
2. **抖动**：添加 ±30% 随机抖动避免惊群效应
3. **TerminalQuotaError 触发模型降级**：日限额时尝试切换到备用模型
4. **RetryableQuotaError 使用建议延迟**：优先使用 API 返回的建议延迟时间

---

### 3.4 Final Warning Turn 恢复机制

#### 职责定位

当达到最大轮次限制时，给 LLM 一个"最后陈述"的机会，优雅地结束会话而非异常退出。

#### 关键代码

```typescript
// gemini-cli/packages/core/src/core/client.ts
const MAX_TURNS = 100;  // 全局最大回合数

private checkTermination(): boolean {
  if (this.sessionTurnCount >= MAX_TURNS) {
    this.handleFinalWarningTurn();
    return true;
  }
  return false;
}

private async handleFinalWarningTurn(): Promise<void> {
  // 1. 向 LLM 发送最终警告提示
  const warningPrompt =
    `You have reached the maximum number of turns (${MAX_TURNS}). ` +
    `Please summarize your progress and provide a final response to the user.`;

  // 2. 禁用所有工具调用
  const disabledTools = this.disableAllTools();

  // 3. 执行最后一轮
  const finalResponse = await this.sendMessageStream(warningPrompt, {
    tools: disabledTools,
  });

  // 4. 返回最终响应给用户
  return finalResponse;
}
```

**设计亮点**：
1. **不直接中断对话**：给 LLM 一个总结和收尾的机会
2. **禁用工具防止无限循环**：确保 Final Warning Turn 不会触发新的工具调用
3. **优雅地结束会话**：保持良好的用户体验，避免数据丢失

---

## 4. 端到端数据流转

### 4.1 工具错误处理完整流程

```mermaid
sequenceDiagram
    participant U as 用户/LLM
    participant T as ToolWrapper
    participant E as ErrorClassifier
    participant R as RetryManager
    participant F as FinalWarningHandler

    U->>T: 1. 调用工具
    activate T

    T->>T: 2. 参数验证
    alt 参数验证失败
        T-->>U: 返回 INVALID_TOOL_PARAMS
    else 参数验证通过
        T->>T: 3. 执行工具

        alt 执行成功
            T-->>U: 返回结果
        else 执行失败
            T->>E: 4. 分类错误
            activate E

            E->>E: 4.1 解析错误类型
            alt 配额错误
                E->>E: 4.2 区分 Terminal/Retryable
                E-->>R: 返回分类错误
            else 其他错误
                E-->>T: 返回 ToolErrorType
            end
            deactivate E

            alt TerminalQuotaError
                R->>R: 5.1 尝试模型降级
                alt 降级成功
                    R->>R: 重置尝试次数
                    R->>T: 重试
                else 降级失败
                    R-->>U: 抛出错误
                end
            else RetryableQuotaError
                R->>R: 5.2 使用建议延迟
                R->>T: 延迟后重试
            else 其他可重试错误
                R->>R: 5.3 指数退避重试
                R->>T: 重试
            else 不可重试错误
                R-->>U: 返回错误
            end
        end
    end

    alt 达到 MAX_TURNS
        T->>F: 6. 触发 Final Warning
        activate F
        F->>F: 6.1 禁用所有工具
        F->>F: 6.2 发送总结提示
        F-->>U: 6.3 返回最终响应
        deactivate F
    end

    deactivate T
```

### 4.2 数据变换详情

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 参数验证 | 原始参数 | Schema 验证 | 验证结果/参数错误 | `gemini-cli/packages/core/src/tools/tool-error.ts` |
| 错误分类 | 原始错误 | 解析 quotaId/reason | Terminal/Retryable/其他 | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` |
| 重试决策 | 分类错误 | 检查重试条件 | 重试/降级/抛出 | `gemini-cli/packages/core/src/utils/retry.ts` |
| 终止处理 | 达到 MAX_TURNS | 禁用工具+总结提示 | 最终响应 | `gemini-cli/packages/core/src/core/client.ts` |

---

## 5. 关键代码实现

### 5.1 核心数据结构

```typescript
// gemini-cli/packages/core/src/tools/tool-error.ts
export enum ToolErrorType {
  POLICY_VIOLATION = 'policy_violation',
  INVALID_TOOL_PARAMS = 'invalid_tool_params',
  UNKNOWN = 'unknown',
  UNHANDLED_EXCEPTION = 'unhandled_exception',
  TOOL_NOT_REGISTERED = 'tool_not_registered',
  EXECUTION_FAILED = 'execution_failed',
  FILE_NOT_FOUND = 'file_not_found',
  FILE_WRITE_FAILURE = 'file_write_failure',
  READ_CONTENT_FAILURE = 'read_content_failure',
  PERMISSION_DENIED = 'permission_denied',
  NO_SPACE_LEFT = 'no_space_left',
  PATH_NOT_IN_WORKSPACE = 'path_not_in_workspace',
  FILE_TOO_LARGE = 'file_too_large',
  SHELL_EXECUTE_ERROR = 'shell_execute_error',
  MCP_TOOL_ERROR = 'mcp_tool_error',
  STOP_EXECUTION = 'stop_execution',
}
```

**字段说明**：
| 字段 | 类型 | 用途 |
|-----|------|------|
| `NO_SPACE_LEFT` | `string` | 唯一致命错误，触发立即终止 |
| `INVALID_TOOL_PARAMS` | `string` | 参数验证失败，返回给 LLM 自纠正 |
| `POLICY_VIOLATION` | `string` | 内容策略违规 |

### 5.2 配额错误分类代码

```typescript
// gemini-cli/packages/core/src/utils/googleQuotaErrors.ts:45-85
export function classifyGoogleError(error: unknown): unknown {
  const googleApiError = parseGoogleApiError(error);
  const status = googleApiError?.code ?? getErrorStatus(error);

  // 404 错误 → ModelNotFoundError
  if (status === 404) {
    return new ModelNotFoundError(message, status);
  }

  // 检查 QuotaFailure 中的日限额
  const quotaFailure = googleApiError.details.find(
    (d): d is QuotaFailure =>
      d['@type'] === 'type.googleapis.com/google.rpc.QuotaFailure',
  );

  if (quotaFailure) {
    for (const violation of quotaFailure.violations) {
      const quotaId = violation.quotaId ?? '';
      // 日限额 = 终端错误
      if (quotaId.includes('PerDay') || quotaId.includes('Daily')) {
        return new TerminalQuotaError(
          `You have exhausted your daily quota on this model.`,
          googleApiError,
        );
      }
    }
  }

  // 分钟限额 = 可重试
  if (quotaId.includes('PerMinute')) {
    return new RetryableQuotaError(
      `${googleApiError.message}\nSuggested retry after 60s.`,
      googleApiError,
      60,
    );
  }
}
```

**代码要点**：
1. **quotaId 模式匹配**：通过字符串包含检查区分日限额和分钟限额
2. **错误对象包装**：将原始错误包装为具有明确语义的类型化错误
3. **建议延迟传递**：分钟限额附带 60 秒建议延迟

### 5.3 关键调用链

```text
retryWithBackoff()          [gemini-cli/packages/core/src/utils/retry.ts:45]
  -> classifyGoogleError()  [gemini-cli/packages/core/src/utils/googleQuotaErrors.ts:45]
    -> parseGoogleApiError() [gemini-cli/packages/core/src/utils/googleQuotaErrors.ts:20]
      - 解析 error.details
      - 提取 QuotaFailure
      - 遍历 violations

handleFinalWarningTurn()    [gemini-cli/packages/core/src/core/client.ts:280]
  -> disableAllTools()      [gemini-cli/packages/core/src/core/client.ts:275]
  -> sendMessageStream()    [gemini-cli/packages/core/src/core/client.ts:150]
    - 发送总结提示
    - 返回最终响应
```

---

## 6. 设计意图与 Trade-off

### 6.1 Gemini CLI 的选择

| 维度 | Gemini CLI 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 错误分类 | 15+ 种细粒度 ToolErrorType | 简单成功/失败二分 | 精准错误报告，但维护成本较高 |
| 配额处理 | Terminal/Retryable 双类 | 统一重试或不重试 | 智能恢复策略，但需解析 quotaId |
| 终止处理 | Final Warning Turn | 直接抛出异常 | 优雅结束，但消耗额外轮次 |
| 重试策略 | 指数退避 + 抖动 | 固定延迟 | 避免惊群，但延迟不确定 |

### 6.2 为什么这样设计？

**核心问题**：如何在复杂的工具调用场景中实现优雅的错误恢复？

**Gemini CLI 的解决方案**：
- 代码依据：`gemini-cli/packages/core/src/utils/googleQuotaErrors.ts:45`
- 设计意图：通过细粒度错误分类指导恢复策略，避免一刀切
- 带来的好处：
  - 日限额立即切换模型，不浪费重试次数
  - 分钟限额延迟后自动恢复，无需用户干预
  - 其他错误返回给 LLM，支持自纠正
- 付出的代价：
  - 需维护 quotaId 模式列表
  - 错误分类逻辑与 Google API 紧密耦合

### 6.3 与其他项目的对比

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| Gemini CLI | 细粒度错误分类 + Final Warning Turn | 需要优雅降级和复杂恢复策略 |
| Codex | 基于 Actor 的消息驱动错误处理 | 高并发、需要严格隔离 |
| Kimi CLI | Checkpoint 回滚机制 | 需要状态回滚的对话场景 |
| OpenCode | 简单重试 + 超时控制 | 轻量级、快速响应 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 达到 MAX_TURNS | `sessionTurnCount >= 100` | `gemini-cli/packages/core/src/core/client.ts:298` |
| 致命工具错误 | `errorType === NO_SPACE_LEFT` | `gemini-cli/packages/core/src/tools/tool-error.ts:55` |
| TerminalQuotaError 且降级失败 | 日限额且无可用的备用模型 | `gemini-cli/packages/core/src/utils/retry.ts:120` |

### 7.2 超时/资源限制

```typescript
// gemini-cli/packages/core/src/utils/retry.ts:15-20
const DEFAULT_RETRY_OPTIONS: RetryOptions = {
  maxAttempts: 3,
  initialDelayMs: 5000,    // 5秒初始延迟
  maxDelayMs: 30000,       // 最大30秒
};
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| TerminalQuotaError | 模型降级或抛出 | `gemini-cli/packages/core/src/utils/retry.ts:110-125` |
| RetryableQuotaError | 使用建议延迟重试 | `gemini-cli/packages/core/src/utils/retry.ts:126-135` |
| 网络错误 | 指数退避重试 | `gemini-cli/packages/core/src/utils/retry.ts:136-145` |
| 400 Bad Request | 不重试直接抛出 | `gemini-cli/packages/core/src/utils/retry.ts:85` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 错误类型定义 | `gemini-cli/packages/core/src/tools/tool-error.ts` | 15-30 | ToolErrorType 枚举 |
| 致命错误判定 | `gemini-cli/packages/core/src/tools/tool-error.ts` | 50-55 | isFatalToolError() |
| 配额错误分类 | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` | 45-85 | classifyGoogleError() |
| TerminalQuotaError | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` | 20-35 | 日限额错误类 |
| RetryableQuotaError | `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` | 38-50 | 分钟限额错误类 |
| 重试逻辑 | `gemini-cli/packages/core/src/utils/retry.ts` | 45-100 | retryWithBackoff() |
| 可重试判定 | `gemini-cli/packages/core/src/utils/retry.ts` | 75-90 | isRetryableError() |
| 最大轮次限制 | `gemini-cli/packages/core/src/core/client.ts` | 295-300 | MAX_TURNS 常量 |
| Final Warning | `gemini-cli/packages/core/src/core/client.ts` | 315-335 | handleFinalWarningTurn() |

---

## 9. 延伸阅读

- 前置知识：`docs/gemini-cli/04-gemini-cli-agent-loop.md`
- 相关机制：`docs/gemini-cli/05-gemini-cli-tools-system.md`
- 深度分析：`docs/gemini-cli/06-gemini-cli-mcp-integration.md`

---

*✅ Verified: 基于 gemini-cli/packages/core/src/tools/tool-error.ts、googleQuotaErrors.ts 等源码分析*
*基于版本：gemini-cli (baseline 2026-02-08) | 最后更新：2026-02-24*
