# OpenCode 工具调用错误处理机制

**结论先行**: OpenCode 采用 **Provider-specific 错误解析 + Doom loop 检测 + resetTimeoutOnProgress** 的创新架构，通过 `PermissionNext` 规则引擎实现细粒度权限控制，并支持 `Retry-After` 响应头的三种格式解析（毫秒/秒/HTTP日期），在处理长任务和重复失败场景上有独特优势。

---

## 1. 错误类型体系

### 1.1 Provider 错误解析

位于 `opencode/packages/opencode/src/provider/error.ts`，OpenCode 针对多 Provider 场景实现了统一错误解析：

```typescript
export namespace ProviderError {
  // 上下文溢出检测模式（覆盖 12+ 家 Provider）
  const OVERFLOW_PATTERNS = [
    /prompt is too long/i,                    // Anthropic
    /input is too long for requested model/i, // Amazon Bedrock
    /exceeds the context window/i,            // OpenAI
    /input token count.*exceeds the maximum/i,// Google (Gemini)
    /maximum prompt length is \d+/i,          // xAI (Grok)
    /reduce the length of the messages/i,     // Groq
    /maximum context length is \d+ tokens/i,  // OpenRouter, DeepSeek
    /exceeds the limit of \d+/i,             // GitHub Copilot
    /exceeded model token limit/i,            // Kimi For Coding, Moonshot
    /context[_ ]length[_ ]exceeded/i,         // Generic fallback
  ]

  export type ParsedAPICallError =
    | { type: "context_overflow"; message: string; responseBody?: string }
    | { type: "api_error"; message: string; statusCode?: number; isRetryable: boolean; responseHeaders?: Record<string, string>; responseBody?: string; metadata?: Record<string, string> }

  export function parseAPICallError(input: { providerID: string; error: APICallError }): ParsedAPICallError {
    const m = message(input.providerID, input.error)
    if (isOverflow(m)) {
      return { type: "context_overflow", message: m, responseBody: input.error.responseBody }
    }
    return {
      type: "api_error",
      message: m,
      statusCode: input.error.statusCode,
      isRetryable: input.providerID.startsWith("openai") ? isOpenAiErrorRetryable(input.error) : input.error.isRetryable,
      responseHeaders: input.error.responseHeaders,
      responseBody: input.error.responseBody,
      metadata: input.error.url ? { url: input.error.url } : undefined,
    }
  }
}
```

### 1.2 流式错误解析

```typescript
export type ParsedStreamError =
  | { type: "context_overflow"; message: string; responseBody: string }
  | { type: "api_error"; message: string; isRetryable: false; responseBody: string }

export function parseStreamError(input: unknown): ParsedStreamError | undefined {
  const body = json(input)
  if (!body) return
  if (body.type !== "error") return

  switch (body?.error?.code) {
    case "context_length_exceeded":
      return { type: "context_overflow", message: "Input exceeds context window of this model", responseBody }
    case "insufficient_quota":
      return { type: "api_error", message: "Quota exceeded. Check your plan and billing details.", isRetryable: false, responseBody }
    case "usage_not_included":
      return { type: "api_error", message: "To use Codex with your ChatGPT plan, upgrade to Plus...", isRetryable: false, responseBody }
    case "invalid_prompt":
      return { type: "api_error", message: body?.error?.message || "Invalid prompt.", isRetryable: false, responseBody }
  }
}
```

---

## 2. 重试机制

### 2.1 SessionRetry 配置

位于 `opencode/packages/opencode/src/session/retry.ts`：

```typescript
export namespace SessionRetry {
  export const RETRY_INITIAL_DELAY = 2000
  export const RETRY_BACKOFF_FACTOR = 2
  export const RETRY_MAX_DELAY_NO_HEADERS = 30_000  // 30秒
  export const RETRY_MAX_DELAY = 2_147_483_647      // max 32-bit signed integer

  export async function sleep(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const abortHandler = () => {
        clearTimeout(timeout)
        reject(new DOMException("Aborted", "AbortError"))
      }
      const timeout = setTimeout(() => {
        signal.removeEventListener("abort", abortHandler)
        resolve()
      }, Math.min(ms, RETRY_MAX_DELAY))
      signal.addEventListener("abort", abortHandler, { once: true })
    })
  }
}
```

### 2.2 延迟计算（支持 Retry-After 三种格式）

```typescript
export function delay(attempt: number, error?: MessageV2.APIError) {
  if (error) {
    const headers = error.data.responseHeaders
    if (headers) {
      // 1. retry-after-ms: 毫秒格式
      const retryAfterMs = headers["retry-after-ms"]
      if (retryAfterMs) {
        const parsedMs = Number.parseFloat(retryAfterMs)
        if (!Number.isNaN(parsedMs)) return parsedMs
      }

      // 2. retry-after: 秒或 HTTP 日期格式
      const retryAfter = headers["retry-after"]
      if (retryAfter) {
        // 尝试解析为秒
        const parsedSeconds = Number.parseFloat(retryAfter)
        if (!Number.isNaN(parsedSeconds)) return Math.ceil(parsedSeconds * 1000)

        // 尝试解析为 HTTP 日期格式
        const parsed = Date.parse(retryAfter) - Date.now()
        if (!Number.isNaN(parsed) && parsed > 0) return Math.ceil(parsed)
      }

      return RETRY_INITIAL_DELAY * Math.pow(RETRY_BACKOFF_FACTOR, attempt - 1)
    }
  }

  // 无响应头时：指数退避，最大 30 秒
  return Math.min(RETRY_INITIAL_DELAY * Math.pow(RETRY_BACKOFF_FACTOR, attempt - 1), RETRY_MAX_DELAY_NO_HEADERS)
}
```

### 2.3 可重试判定

```typescript
export function retryable(error: ReturnType<NamedError["toObject"]>) {
  // 上下文溢出错误不重试
  if (MessageV2.ContextOverflowError.isInstance(error)) return undefined

  if (MessageV2.APIError.isInstance(error)) {
    if (!error.data.isRetryable) return undefined
    if (error.data.responseBody?.includes("FreeUsageLimitError"))
      return `Free usage exceeded, add credits https://opencode.ai/zen`
    return error.data.message.includes("Overloaded") ? "Provider is overloaded" : error.data.message
  }

  // 解析 JSON 错误体
  const json = iife(() => {
    try {
      if (typeof error.data?.message === "string") {
        return JSON.parse(error.data.message)
      }
      return JSON.parse(error.data.message)
    } catch { return undefined }
  })

  if (!json || typeof json !== "object") return undefined
  const code = typeof json.code === "string" ? json.code : ""

  if (json.type === "error" && json.error?.type === "too_many_requests") {
    return "Too Many Requests"
  }
  if (code.includes("exhausted") || code.includes("unavailable")) {
    return "Provider is overloaded"
  }
  if (json.type === "error" && json.error?.code?.includes("rate_limit")) {
    return "Rate Limited"
  }
  return JSON.stringify(json)
}
```

---

## 3. 权限与审批机制

### 3.1 PermissionNext 规则引擎

位于 `opencode/packages/opencode/src/permission/next.ts`：

```typescript
export namespace PermissionNext {
  export const Action = z.enum(["allow", "deny", "ask"])
  export type Action = z.infer<typeof Action>

  export const Rule = z.object({
    permission: z.string(),
    pattern: z.string(),
    action: Action,
  })

  export const Ruleset = Rule.array()

  export function fromConfig(permission: Config.Permission): Ruleset {
    const ruleset: Ruleset = []
    for (const [key, value] of Object.entries(permission)) {
      if (typeof value === "string") {
        ruleset.push({ permission: key, action: value, pattern: "*" })
        continue
      }
      ruleset.push(
        ...Object.entries(value).map(([pattern, action]) => ({
          permission: key,
          pattern: expand(pattern),
          action,
        }))
      )
    }
    return ruleset
  }
}
```

### 3.2 审批流程

```typescript
export const ask = fn(
  Request.partial({ id: true }).extend({ ruleset: Ruleset }),
  async (input) => {
    const s = await state()
    const { ruleset, ...request } = input
    for (const pattern of request.patterns ?? []) {
      const rule = evaluate(request.permission, pattern, ruleset, s.approved)
      log.info("evaluated", { permission: request.permission, pattern, action: rule })

      if (rule.action === "deny")
        throw new DeniedError(ruleset.filter((r) => Wildcard.match(request.permission, r.permission)))

      if (rule.action === "ask") {
        const id = input.id ?? Identifier.ascending("permission")
        return new Promise<void>((resolve, reject) => {
          const info: Request = { id, ...request }
          s.pending[id] = { info, resolve, reject }
          Bus.publish(Event.Asked, info)
        })
      }

      if (rule.action === "allow") continue
    }
  }
)
```

### 3.3 三种审批结果

```typescript
export const Reply = z.enum(["once", "always", "reject"])

export const reply = fn(
  z.object({ requestID: Identifier.schema("permission"), reply: Reply, message: z.string().optional() }),
  async (input) => {
    const s = await state()
    const existing = s.pending[input.requestID]
    if (!existing) return
    delete s.pending[input.requestID]

    if (input.reply === "reject") {
      existing.reject(input.message ? new CorrectedError(input.message) : new RejectedError())
      // 拒绝同一会话的所有待处理权限请求
      for (const [id, pending] of Object.entries(s.pending)) {
        if (pending.info.sessionID === sessionID) {
          delete s.pending[id]
          pending.reject(new RejectedError())
        }
      }
      return
    }

    if (input.reply === "once") {
      existing.resolve()
      return
    }

    if (input.reply === "always") {
      // 持久化到数据库
      s.approved.push({ permission: existing.info.permission, pattern: existing.info.patterns[0], action: "allow" })
      Database.use((db) => db.insert(PermissionTable).values({ project_id: projectID, data: s.approved }).onConflictDoUpdate({ target: PermissionTable.project_id, set: { data: s.approved } }))
      existing.resolve()
    }
  }
)
```

### 3.4 默认权限规则

```typescript
const defaults = PermissionNext.fromConfig({
  "*": "allow",
  doom_loop: "ask",           // Doom loop 检测触发时询问
  external_directory: {
    "*": "ask",
    ...Object.fromEntries(whitelistedDirs.map((dir) => [dir, "allow"])),
  },
  question: "deny",
  plan_enter: "deny",
  plan_exit: "deny",
  read: {
    "*": "allow",
    "*.env": "ask",          // .env 文件读取需要确认
    "*.env.*": "ask",
    "*.env.example": "allow",
  },
})
```

---

## 4. Doom Loop 检测

### 4.1 检测机制

Doom loop 检测是 OpenCode 的创新特性，用于检测重复相同的工具调用模式：

```typescript
// 默认规则中启用 doom_loop 检测
const defaults = PermissionNext.fromConfig({
  "*": "allow",
  doom_loop: "ask",  // 当检测到 doom loop 时询问用户
})
```

### 4.2 检测逻辑

```typescript
// 伪代码：基于最后 N 次工具调用检测重复模式
class DoomLoopDetector {
  private recentCalls: ToolCallRecord[] = []
  private readonly DETECTION_WINDOW = 3  // 检测最近 3 次调用

  detectDoomLoop(): boolean {
    const recent = this.recentCalls.slice(-this.DETECTION_WINDOW)

    // 检查是否重复调用相同工具且参数相似
    if (recent.length < this.DETECTION_WINDOW) return false

    const first = recent[0]
    return recent.every(call =>
      call.toolName === first.toolName &&
      this.areParamsSimilar(call.params, first.params)
    )
  }
}
```

### 4.3 触发后的行为

当检测到 doom loop 时：
1. 触发 `doom_loop` 权限检查（action: "ask"）
2. 暂停执行等待用户确认
3. 用户可选择继续、修改提示词或中止

---

## 5. Token 溢出处理

### 5.1 上下文溢出检测

```typescript
// 在 parseAPICallError 中检测
if (isOverflow(m)) {
  return { type: "context_overflow", message: m, responseBody: input.error.responseBody }
}

// OVERFLOW_PATTERNS 覆盖 12+ 家 Provider
const OVERFLOW_PATTERNS = [
  /prompt is too long/i,                    // Anthropic
  /exceeds the context window/i,            // OpenAI
  /input token count.*exceeds the maximum/i,// Google
  // ...
]
```

### 5.2 压缩策略

```typescript
// 专用 compaction agent
const compaction = {
  name: "compaction",
  mode: "primary",
  hidden: true,
  prompt: PROMPT_COMPACTION,
  permission: PermissionNext.merge(
    defaults,
    PermissionNext.fromConfig({ "*": "deny" })  // compaction 期间禁用所有工具
  ),
}
```

### 5.3 Prune + Compaction 流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    OpenCode 上下文压缩流程                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   检测到溢出 (isOverflow)                                        │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────┐                                              │
│   │   Prune     │  裁剪早期消息                                 │
│   │  (早期历史) │                                              │
│   └──────┬──────┘                                              │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │ Compaction  │  调用 compaction agent 生成摘要               │
│   │   Agent     │                                              │
│   └──────┬──────┘                                              │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │ 重建上下文  │  用摘要替换原始历史                            │
│   └─────────────┘                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 超时处理

### 6.1 resetTimeoutOnProgress

OpenCode 的创新机制，当工具调用有进度时重置超时计时器：

```typescript
// 伪代码：基于进度的超时重置
class TimeoutManager {
  private timeoutId: NodeJS.Timeout
  private lastProgressTime: number

  startTimeout(timeoutMs: number, onTimeout: () => void) {
    this.timeoutId = setInterval(() => {
      if (Date.now() - this.lastProgressTime > timeoutMs) {
        onTimeout()
      }
    }, 1000)
  }

  onProgress() {
    this.lastProgressTime = Date.now()  // 重置计时器
  }
}
```

**适用场景**:
- 长时间运行的 bash 命令
- 大文件下载/上传
- 编译构建过程

---

## 7. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `opencode/packages/opencode/src/provider/error.ts` | `ProviderError`多 Provider 错误解析，`OVERFLOW_PATTERNS`上下文溢出检测 |
| `opencode/packages/opencode/src/session/retry.ts` | `SessionRetry`重试逻辑，`delay()`支持 `Retry-After` 三种格式 |
| `opencode/packages/opencode/src/permission/next.ts` | `PermissionNext`规则引擎，`ask()`审批流程，三种回复类型 |
| `opencode/packages/opencode/src/agent/agent.ts` | Agent 配置，`doom_loop` 权限规则，compaction agent |

---

## 8. 设计亮点与启示

### 8.1 Provider 错误解析的优势

| 特性 | 实现 | 价值 |
|------|------|------|
| 多 Provider 支持 | OVERFLOW_PATTERNS 覆盖 12+ 家 | 统一处理不同 Provider 的上下文溢出 |
| 流式错误解析 | parseStreamError | 实时检测流中的错误 |
| Provider 特定逻辑 | isOpenAiErrorRetryable | 针对特定 Provider 优化 |

### 8.2 Retry-After 三种格式支持

| 格式 | 示例 | 支持情况 |
|------|------|---------|
| 毫秒 | `retry-after-ms: 5000` | ✅ 支持 |
| 秒 | `retry-after: 10` | ✅ 支持 |
| HTTP 日期 | `retry-after: Wed, 21 Oct 2025 07:28:00 GMT` | ✅ 支持 |

### 8.3 权限系统的灵活性

```typescript
// 三种 action 类型
"allow"  // 直接允许
"deny"   // 直接拒绝
"ask"    // 询问用户

// 三种回复类型
"once"   // 仅本次允许
"always" // 始终允许（持久化）
"reject" // 拒绝
```

### 8.4 Doom Loop 检测

相比简单的重复检测，Doom loop 检测：
1. **行为感知**: 检测 LLM 陷入无效循环
2. **用户介入**: 在关键点询问而非自动继续
3. **会话级**: 拒绝时会取消同一会话的所有待处理权限

---

*文档版本: 2026-02-21*
*基于代码版本: opencode (baseline 2026-02-08)*
