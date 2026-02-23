# Memory Context（Qwen Code）

本文分析 Qwen Code 的 Memory Context 管理机制，包括聊天历史管理、上下文压缩、Token 管理和 IDE 上下文集成。

---

## 1. 先看全局（流程图）

### 1.1 上下文管理架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        上下文层级                                    │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Level 4: System Context                                       │  │
│  │  - System prompt (core prompts)                                │  │
│  │  - 工具声明 (Function Declarations)                             │  │
│  │  - 系统提醒 (subagent/plan mode reminders)                      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Level 3: Chat History (GeminiChat)                            │  │
│  │  (packages/core/src/core/geminiChat.ts)                        │  │
│  │  - history: Content[]  (API 格式)                              │  │
│  │  - 管理 addHistory/setHistory/stripThoughts                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Level 2: Compression Service                                  │  │
│  │  (packages/core/src/services/                                  │  │
│  │   chatCompressionService.ts:78)                                │  │
│  │  - 阈值: COMPRESSION_TOKEN_THRESHOLD = 0.7                     │  │
│  │  - 保留: COMPRESSION_PRESERVE_THRESHOLD = 0.3                  │  │
│  │  - 自动总结早期对话                                             │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Level 1: IDE Context (可选)                                    │  │
│  │  (packages/core/src/ide/ideContext.ts)                         │  │
│  │  - 活跃文件、光标位置、选择区域                                  │  │
│  │  - 增量更新（有工具调用时跳过）                                   │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 上下文压缩流程

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     上下文压缩时序                                    │
└──────────────────────────────────────────────────────────────────────┘

sendMessageStream 调用前
         │
         ▼
┌─────────────────────┐
│ tryCompressChat()   │ ◄── 检查是否需要压缩
│ (client.ts:434)     │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │ 当前 tokens  │
    │ > 0.7 * limit│
    ▼              ▼ No
┌────────┐   ┌─────────────┐
│ 需要压缩 │   │ NOOP 跳过   │
└───┬────┘   └─────────────┘
    │
    ▼
┌─────────────────────────┐
│ ChatCompressionService  │
│ .compress()             │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 1. findCompressSplitPoint│
│    - 保留最近 30%        │
│    - 在 user message 边界分割│
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 2. 生成 summary         │
│    调用模型总结早期对话   │
│    "Summarize the above │
│     conversation..."    │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 3. 重建 history         │
│    [summary,            │
│     preserved_messages] │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ yield ChatCompressed    │
│ 事件通知 UI              │
└─────────────────────────┘
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道上下文分四层，压缩阈值 0.7，保留 0.3。
- **3 分钟版**：看 `1.1` + `1.2` + `3.1` 节，了解压缩流程和 IDE 上下文注入。
- **10 分钟版**：通读全文，掌握 Token 管理、历史操作和压缩策略。

### 2.1 一句话定义

Qwen Code 的 Memory Context 采用「**分层管理 + 自动压缩**」策略：GeminiChat 管理 API 历史，ChatCompressionService 在 token 超过 70% 阈值时自动总结压缩，IDE 上下文增量注入保持实时同步。

---

## 3. 核心组件

### 3.1 GeminiChat

✅ **Verified**: `qwen-code/packages/core/src/core/geminiChat.ts`

```typescript
export class GeminiChat {
  private history: Content[] = [];
  private lastPromptTokenCount = 0;

  constructor(private readonly config: Config) {}

  // 添加历史记录
  addHistory(content: Content): void {
    this.history.push(content);
  }

  // 设置历史（用于恢复或压缩后重建）
  setHistory(history: Content[]): void {
    this.history = history;
  }

  // 获取历史（支持 curated 模式过滤）
  getHistory(curated = false): Content[] {
    if (!curated) return this.history;

    // curated 模式：移除可能导致问题的记录
    return this.history.filter((content) => {
      // 过滤掉空内容
      if (!content.parts || content.parts.length === 0) return false;
      // 保留有效内容
      return true;
    });
  }

  // 移除思考内容（减少 token 消耗）
  stripThoughtsFromHistory(): void {
    for (const content of this.history) {
      if (content.parts) {
        for (const part of content.parts) {
          if ('thought' in part) {
            delete (part as { thought?: unknown }).thought;
          }
          if ('thoughtSignature' in part) {
            delete (part as { thoughtSignature?: unknown }).thoughtSignature;
          }
        }
      }
    }
  }

  // 发送流式消息
  async *sendMessageStream(
    model: string,
    params: SendMessageParameters,
    promptId: string,
  ): AsyncGenerator<StreamEvent> {
    // 流式处理，包含重试逻辑
    // ...
  }
}
```

### 3.2 ChatCompressionService

✅ **Verified**: `qwen-code/packages/core/src/services/chatCompressionService.ts:78`

```typescript
/**
 * 压缩阈值：token 数占上下文限制的比率
 * 超过此值触发压缩（默认 70%）
 */
export const COMPRESSION_TOKEN_THRESHOLD = 0.7;

/**
 * 保留阈值：压缩后保留的历史比例
 * 保留最近 30% 的完整对话
 */
export const COMPRESSION_PRESERVE_THRESHOLD = 0.3;

export class ChatCompressionService {
  async compress(
    chat: GeminiChat,
    promptId: string,
    force: boolean,
    model: string,
    config: Config,
    hasFailedCompressionAttempt: boolean,
  ): Promise<{ newHistory: Content[] | null; info: ChatCompressionInfo }> {
    const curatedHistory = chat.getHistory(true);
    const threshold =
      config.getChatCompression()?.contextPercentageThreshold ??
      COMPRESSION_TOKEN_THRESHOLD;

    // 空历史或禁用压缩
    if (curatedHistory.length === 0 || threshold <= 0) {
      return {
        newHistory: null,
        info: { originalTokenCount: 0, newTokenCount: 0, compressionStatus: CompressionStatus.NOOP },
      };
    }

    // 检查是否已失败过（除非强制）
    if (hasFailedCompressionAttempt && !force) {
      return {
        newHistory: null,
        info: { originalTokenCount: 0, newTokenCount: 0, compressionStatus: CompressionStatus.NOOP },
      };
    }

    const originalTokenCount = uiTelemetryService.getLastPromptTokenCount();

    // 检查是否超过阈值
    if (!force) {
      const contextLimit =
        config.getContentGeneratorConfig()?.contextWindowSize ??
        DEFAULT_TOKEN_LIMIT;
      if (originalTokenCount < threshold * contextLimit) {
        return {
          newHistory: null,
          info: { originalTokenCount, newTokenCount: originalTokenCount, compressionStatus: CompressionStatus.NOOP },
        };
      }
    }

    // 计算分割点
    const splitPoint = findCompressSplitPoint(
      curatedHistory,
      1 - COMPRESSION_PRESERVE_THRESHOLD,  // 保留 30%
    );

    const historyToCompress = curatedHistory.slice(0, splitPoint);
    const historyToKeep = curatedHistory.slice(splitPoint);

    if (historyToCompress.length === 0) {
      return { newHistory: null, info: {...} };
    }

    // 生成总结
    const summaryResponse = await config.getContentGenerator().generateContent(
      {
        model,
        contents: [
          ...historyToCompress,
          {
            role: 'user',
            parts: [{ text: getCompressionPrompt() }],
          },
        ],
      },
    );

    const summaryText = getResponseText(summaryResponse);
    const newTokenCount = summaryResponse.usageMetadata?.totalTokenCount ?? 0;

    // 重建历史
    const newHistory: Content[] = [
      {
        role: 'user',
        parts: [{ text: `Previous conversation summary:\n${summaryText}` }],
      },
      ...historyToKeep,
    ];

    return {
      newHistory,
      info: {
        originalTokenCount,
        newTokenCount,
        compressionStatus: CompressionStatus.COMPRESSED,
      },
    };
  }
}

// 计算分割点
export function findCompressSplitPoint(contents: Content[], fraction: number): number {
  const charCounts = contents.map((c) => JSON.stringify(c).length);
  const totalCharCount = charCounts.reduce((a, b) => a + b, 0);
  const targetCharCount = totalCharCount * fraction;

  let lastSplitPoint = 0;
  let cumulativeCharCount = 0;

  for (let i = 0; i < contents.length; i++) {
    const content = contents[i];
    // 只在 user message 处分割（非 functionResponse）
    if (
      content.role === 'user' &&
      !content.parts?.some((p) => !!p.functionResponse)
    ) {
      if (cumulativeCharCount >= targetCharCount) {
        return i;
      }
      lastSplitPoint = i;
    }
    cumulativeCharCount += charCounts[i];
  }

  return lastSplitPoint;
}
```

### 3.3 IDE 上下文集成

✅ **Verified**: `qwen-code/packages/core/src/core/client.ts:320`

```typescript
export class GeminiClient {
  private lastSentIdeContext: IdeContext | undefined;
  private forceFullIdeContext = true;

  private getIdeContextParts(
    includeFullContext: boolean,
  ): { contextParts: string[]; newIdeContext: IdeContext } {
    const currentIdeContext = ideContextStore.getContext();

    if (includeFullContext) {
      // 发送完整上下文
      const contextLines = [
        'Current editor context:',
        `  Active file: ${currentIdeContext.activeFile?.path || 'none'}`,
        `  Cursor: line ${currentIdeContext.cursor?.line}, col ${currentIdeContext.cursor?.column}`,
        // ...
      ];
      return { contextParts: contextLines, newIdeContext: currentIdeContext };
    } else {
      // 增量更新：只发送变化
      const lastContext = this.lastSentIdeContext;
      const changeLines: string[] = [];

      // 检查活跃文件变化
      if (currentIdeContext.activeFile?.path !== lastContext?.activeFile?.path) {
        changeLines.push(`Active file changed: ${currentIdeContext.activeFile?.path || 'none'}`);
      }

      // 检查选择区域变化
      if (currentIdeContext.selection?.text !== lastContext?.selection?.text) {
        changeLines.push(`Selection changed: ${currentIdeContext.selection?.text?.slice(0, 100)}...`);
      }

      return { contextParts: changeLines, newIdeContext: currentIdeContext };
    }
  }

  async *sendMessageStream(...) {
    // ... 其他代码 ...

    // 检查是否有 pending tool call
    const history = this.getHistory();
    const lastMessage = history[history.length - 1];
    const hasPendingToolCall =
      !!lastMessage &&
      lastMessage.role === 'model' &&
      lastMessage.parts?.some((p) => 'functionCall' in p);

    // 仅在无 pending tool call 时注入 IDE 上下文
    // （API 要求 functionResponse 紧跟 functionCall）
    if (this.config.getIdeMode() && !hasPendingToolCall) {
      const { contextParts, newIdeContext } = this.getIdeContextParts(
        this.forceFullIdeContext || history.length === 0,
      );
      if (contextParts.length > 0) {
        this.getChat().addHistory({
          role: 'user',
          parts: [{ text: contextParts.join('\n') }],
        });
      }
      this.lastSentIdeContext = newIdeContext;
      this.forceFullIdeContext = false;
    }

    // ... 继续处理 ...
  }
}
```

---

## 4. Token 管理

### 4.1 Token 限制层级

| 限制 | 配置项 | 默认值 | 说明 |
|------|--------|--------|------|
| 模型上下文 | `contextWindowSize` | 128K | Gemini 模型限制 |
| 压缩阈值 | `contextPercentageThreshold` | 0.7 | 触发压缩 |
| 会话限制 | `sessionTokenLimit` | 0（无限制）| 硬上限 |
| 单次限制 | `maxOutputTokens` | 8192 | 模型输出限制 |

### 4.2 Token 使用监控

```typescript
// 通过 usageMetadata 跟踪
type GenerateContentResponseUsageMetadata = {
  promptTokenCount: number;      // 输入 tokens
  candidatesTokenCount: number;  // 输出 tokens
  totalTokenCount: number;       // 总计
};

// UI 遥测服务记录
uiTelemetryService.getLastPromptTokenCount();
```

---

## 5. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 历史未压缩 | 检查阈值设置 | `chatCompressionService.ts:22` |
| 压缩后丢失上下文 | 检查分割点计算 | `findCompressSplitPoint` |
| IDE 上下文未更新 | 检查 hasPendingToolCall | `client.ts:468` |
| Token 超限 | 检查 sessionTokenLimit | `client.ts:443` |
| 思考内容未清理 | 检查 stripThoughtsFromHistory | `geminiChat.ts` |
| 恢复后历史错乱 | 检查 buildApiHistoryFromConversation | `sessionService.ts` |

---

## 6. 架构特点

### 6.1 压缩策略优势

```
策略：保留最近 30% + 总结 70%

优势：
1. 保留近期上下文（精确信息）
2. 总结早期对话（节省 tokens）
3. 在 user message 边界分割（避免破坏对话结构）
4. 支持强制压缩（失败后可重试）

边界条件：
- 不在 functionResponse 后分割（API 约束）
- 压缩失败标记，避免重复失败
- 空历史或禁用压缩时跳过
```

### 6.2 IDE 上下文注入策略

```
时机：sendMessageStream 调用前

条件：
1. IDE 模式启用
2. 无 pending tool call（API 约束）

策略：
1. 首次或强制时发送完整上下文
2. 后续仅发送增量变化
3. 更新 lastSentIdeContext 用于比较
```

---

## 7. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 压缩阈值 | 0.7 | ✅ 相同 |
| 保留比例 | 0.3 | ✅ 相同 |
| IDE 上下文 | 支持 | ✅ 增强 |
| 思考清理 | 支持 | ✅ 继承 |
| Token 限制 | 支持 | ✅ 继承 |

---

## 8. 总结

Qwen Code 的 Memory Context 管理特点：

1. **分层架构** - System/Chat/Compression/IDE 四层管理
2. **智能压缩** - 70% 阈值触发，保留 30% 近期上下文
3. **IDE 同步** - 增量注入，实时反映编辑器状态
4. **Token 监控** - 多层限制防止超限
5. **约束感知** - 遵守 API 的 functionCall/functionResponse 邻接约束
