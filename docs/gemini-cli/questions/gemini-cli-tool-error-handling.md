# Gemini CLI 工具调用错误处理机制

**结论先行**: Gemini CLI 采用 **Scheduler 状态机 + Final Warning Turn** 的优雅恢复架构，通过 `ToolErrorType` 枚举定义 15+ 种错误类型，并创新性地将配额错误细分为 `TerminalQuotaError`(日限额) 与 `RetryableQuotaError`(分钟限额)，实现了智能的错误分类与恢复策略。

---

## 1. 错误类型体系

### 1.1 ToolErrorType 枚举

位于 `gemini-cli/packages/core/src/tools/tool-error.ts`，定义了 15+ 种工具错误类型：

```typescript
export enum ToolErrorType {
  // 安全与策略
  POLICY_VIOLATION = 'policy_violation',

  // 通用错误
  INVALID_TOOL_PARAMS = 'invalid_tool_params',
  UNKNOWN = 'unknown',
  UNHANDLED_EXCEPTION = 'unhandled_exception',
  TOOL_NOT_REGISTERED = 'tool_not_registered',
  EXECUTION_FAILED = 'execution_failed',

  // 文件系统错误 (10+ 类型)
  FILE_NOT_FOUND = 'file_not_found',
  FILE_WRITE_FAILURE = 'file_write_failure',
  READ_CONTENT_FAILURE = 'read_content_failure',
  PERMISSION_DENIED = 'permission_denied',
  NO_SPACE_LEFT = 'no_space_left',
  PATH_NOT_IN_WORKSPACE = 'path_not_in_workspace',
  FILE_TOO_LARGE = 'file_too_large',
  // ... 更多文件相关错误

  // Shell 错误
  SHELL_EXECUTE_ERROR = 'shell_execute_error',

  // MCP 错误
  MCP_TOOL_ERROR = 'mcp_tool_error',

  // 其他...
  STOP_EXECUTION = 'stop_execution',
}
```

### 1.2 致命错误判定

```typescript
export function isFatalToolError(errorType?: string): boolean {
  if (!errorType) return false;

  const fatalErrors = new Set<string>([ToolErrorType.NO_SPACE_LEFT]);
  return fatalErrors.has(errorType);
}
```

只有磁盘空间不足被视为致命错误，其他错误都允许 LLM 自纠正。

---

## 2. 配额错误智能分类

### 2.1 配额错误类型定义

位于 `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts`：

```typescript
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

### 2.2 配额错误分类逻辑

```typescript
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

### 2.3 错误分类体系

```
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
│              └─────────┘  └──────────┘                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 重试机制

### 3.1 默认重试配置

位于 `gemini-cli/packages/core/src/utils/retry.ts`：

```typescript
export const DEFAULT_MAX_ATTEMPTS = 3;

const DEFAULT_RETRY_OPTIONS: RetryOptions = {
  maxAttempts: DEFAULT_MAX_ATTEMPTS,
  initialDelayMs: 5000,    // 5秒初始延迟
  maxDelayMs: 30000,       // 最大30秒
  shouldRetryOnError: isRetryableError,
};
```

### 3.2 可重试错误判定

```typescript
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
    // ...
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

### 3.3 带退避的重试逻辑

```typescript
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

---

## 4. Final Warning Turn 恢复机制

### 4.1 最大轮次限制

位于 `gemini-cli/packages/core/src/core/client.ts`：

```typescript
const MAX_TURNS = 100;  // 全局最大回合数
```

### 4.2 终止检查

```typescript
private checkTermination(): boolean {
  if (this.sessionTurnCount >= MAX_TURNS) {
    this.handleFinalWarningTurn();
    return true;
  }
  return false;
}
```

### 4.3 Final Warning Turn 实现

当达到最大轮次限制时，执行 Final Warning Turn：

```typescript
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

**设计亮点**:
- 不直接中断对话，而是给 LLM 一个"最后陈述"的机会
- 禁用工具防止无限循环
- 优雅地结束会话而非异常退出

---

## 5. Token 溢出处理

### 5.1 上下文压缩服务

```typescript
class ChatCompressionService {
  private readonly compressionService: ChatCompressionService;

  async compressChatHistory(history: Content[]): Promise<ChatCompressionInfo> {
    // 检测上下文窗口即将溢出
    if (this.isContextWindowWillOverflow(history)) {
      // 触发压缩
      return await this.compressWithSummary(history);
    }
    return { status: CompressionStatus.NO_COMPRESSION_NEEDED };
  }
}
```

### 5.2 压缩触发条件

```typescript
private isContextWindowWillOverflow(history: Content[]): boolean {
  const estimatedTokens = this.estimateTokenCount(history);
  const limit = tokenLimit.get(this.currentModel);

  // 达到 80% 阈值时触发预防性压缩
  return estimatedTokens > limit * 0.8;
}
```

### 5.3 压缩策略

```typescript
enum CompressionStatus {
  NO_COMPRESSION_NEEDED = 'no_compression_needed',
  COMPRESSION_SUCCESS = 'compression_success',
  COMPRESSION_FAILED = 'compression_failed',
  FORCED_COMPRESSION = 'forced_compression',
}
```

---

## 6. 循环检测机制

### 6.1 LoopDetectionService

```typescript
class LoopDetectionService {
  private recentToolCalls: ToolCallRecord[] = [];
  private readonly LOOP_DETECTION_WINDOW = 5;

  detectLoop(): boolean {
    // 检测最近 N 次工具调用是否形成循环
    const recent = this.recentToolCalls.slice(-this.LOOP_DETECTION_WINDOW);

    // 检查重复模式
    if (this.hasRepeatingPattern(recent)) {
      return true;
    }

    return false;
  }

  private hasRepeatingPattern(calls: ToolCallRecord[]): boolean {
    // 实现模式匹配逻辑
    // 例如: A→B→A→B 或 A→A→A
    // ...
  }
}
```

---

## 7. 工具参数验证错误

### 7.1 参数验证流程

```typescript
class ToolWrapper {
  async execute(params: unknown): Promise<ToolResult> {
    // 1. 参数 Schema 验证
    const validationResult = this.validateParams(params);
    if (!validationResult.valid) {
      return {
        error: {
          type: ToolErrorType.INVALID_TOOL_PARAMS,
          message: validationResult.errorMessage,
        },
      };
    }

    // 2. 执行工具
    try {
      return await this.invoke(params);
    } catch (error) {
      // 3. 错误分类
      return this.classifyExecutionError(error);
    }
  }
}
```

---

## 8. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `gemini-cli/packages/core/src/tools/tool-error.ts` | `ToolErrorType`枚举定义(15+类型)，`isFatalToolError()`判定 |
| `gemini-cli/packages/core/src/utils/googleQuotaErrors.ts` | `TerminalQuotaError`/`RetryableQuotaError`定义，`classifyGoogleError()`智能分类 |
| `gemini-cli/packages/core/src/utils/retry.ts` | `retryWithBackoff()`重试逻辑，`isRetryableError()`判定 |
| `gemini-cli/packages/core/src/core/client.ts` | `MAX_TURNS`限制，`handleFinalWarningTurn()`恢复机制 |
| `gemini-cli/packages/core/src/availability/errorClassification.ts` | `classifyFailureKind()`错误分类策略 |
| `gemini-cli/packages/core/src/services/chatCompressionService.ts` | 上下文压缩服务 |
| `gemini-cli/packages/core/src/services/loopDetectionService.ts` | 循环检测服务 |

---

## 9. 设计亮点与启示

### 9.1 配额错误的智能区分

| 错误类型 | 重试策略 | 延迟时间 | 触发条件 |
|---------|---------|---------|---------|
| TerminalQuotaError | 不可重试/模型降级 | - | 日限额、QUOTA_EXHAUSTED |
| RetryableQuotaError | 可重试 | 10-60秒 | 分钟限额、RATE_LIMIT_EXCEEDED |

这种区分避免了在日限额情况下无意义的重试，同时允许分钟限额快速恢复。

### 9.2 Final Warning Turn 的优雅设计

相比直接抛出异常或强制退出，Final Warning Turn：
1. 给 LLM 一个总结和收尾的机会
2. 保持良好的用户体验
3. 避免数据丢失或状态不一致

### 9.3 工具错误的细致分类

15+ 种 `ToolErrorType` 允许：
- 精准的错误报告
- 针对性的恢复建议
- 只有 `NO_SPACE_LEFT` 被视为致命错误

---

*文档版本: 2026-02-21*
*基于代码版本: gemini-cli (baseline 2026-02-08)*
