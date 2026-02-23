# 循环检测机制（Qwen Code）

本文深入分析 Qwen Code 的循环检测机制，防止 Agent 陷入无限循环。

---

## 1. 循环检测层级

```
┌─────────────────────────────────────────────────────────────────────┐
│                      三层循环检测                                    │
│                                                                      │
│  Level 1: 连续相同工具调用                                            │
│  - 检测相同工具+参数的重复调用                                         │
│  - 阈值：MAX_SAME_TOOL_CALLS                                          │
│                                                                      │
│  Level 2: 内容重复检测 (Chanting)                                     │
│  - 检测模型输出的重复模式                                              │
│  - 阈值：内容相似度 > 90%                                             │
│                                                                      │
│  Level 3: LLM 语义检测                                                │
│  - 长对话后使用 LLM 判断是否在循环                                     │
│  - 阈值：LONG_CONVERSATION_THRESHOLD                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. LoopDetectionService 实现

✅ **Verified**: `qwen-code/packages/core/src/services/loopDetectionService.ts`

```typescript
export class LoopDetectionService {
  private turnCount = 0;
  private lastToolCalls: string[] = [];
  private consecutiveSameToolCalls = 0;
  private contentHistory: string[] = [];

  constructor(private readonly config: Config) {}

  // 重置状态
  reset(promptId: string): void {
    this.turnCount = 0;
    this.lastToolCalls = [];
    this.consecutiveSameToolCalls = 0;
    this.contentHistory = [];
  }

  // Turn 开始前检查
  async turnStarted(signal: AbortSignal): Promise<boolean> {
    this.turnCount++;

    // Level 3: 长对话语义检测
    if (this.turnCount > LONG_CONVERSATION_THRESHOLD) {
      return await this.performLlmLoopCheck(signal);
    }

    return false;
  }

  // 添加事件并检查
  addAndCheck(event: ServerGeminiStreamEvent): boolean {
    // Level 1: 相同工具调用检测
    if (event.type === GeminiEventType.ToolCallRequest) {
      const toolKey = `${event.value.name}:${JSON.stringify(event.value.args)}`;

      if (this.lastToolCalls.includes(toolKey)) {
        this.consecutiveSameToolCalls++;
        if (this.consecutiveSameToolCalls >= MAX_SAME_TOOL_CALLS) {
          return true;  // 检测到循环
        }
      } else {
        this.consecutiveSameToolCalls = 0;
      }

      this.lastToolCalls.push(toolKey);
      if (this.lastToolCalls.length > TOOL_HISTORY_SIZE) {
        this.lastToolCalls.shift();
      }
    }

    // Level 2: 内容重复检测
    if (event.type === GeminiEventType.Content) {
      const content = event.value;
      for (const historyContent of this.contentHistory) {
        if (similarity(content, historyContent) > CHANTING_THRESHOLD) {
          return true;
        }
      }
      this.contentHistory.push(content);
      if (this.contentHistory.length > CONTENT_HISTORY_SIZE) {
        this.contentHistory.shift();
      }
    }

    return false;
  }

  // LLM 语义循环检测
  private async performLlmLoopCheck(signal: AbortSignal): Promise<boolean> {
    const checkPrompt = `
      Analyze the following conversation and determine if the assistant is stuck in a loop.
      Respond with only "YES" or "NO".

      Conversation:
      ${this.contentHistory.slice(-10).join('\n')}
    `;

    const response = await this.config.getContentGenerator().generateContent({
      contents: [{ role: 'user', parts: [{ text: checkPrompt }] }],
    });

    const result = getResponseText(response);
    return result.trim().toUpperCase() === 'YES';
  }
}
```

---

## 3. 阈值配置

```typescript
// 连续相同工具调用阈值
const MAX_SAME_TOOL_CALLS = 3;

// 内容历史保留数量
const CONTENT_HISTORY_SIZE = 10;

// 内容相似度阈值 (chanting)
const CHANTING_THRESHOLD = 0.9;

// 长对话阈值（触发 LLM 检测）
const LONG_CONVERSATION_THRESHOLD = 20;

// 工具历史保留数量
const TOOL_HISTORY_SIZE = 10;
```

---

## 4. 循环检测结果处理

```typescript
// client.ts 中处理循环检测
if (!this.config.getSkipLoopDetection()) {
  const loopDetected = await this.loopDetector.turnStarted(signal);
  if (loopDetected) {
    yield { type: GeminiEventType.LoopDetected };
    return turn;
  }
}

for await (const event of resultStream) {
  if (!this.config.getSkipLoopDetection()) {
    if (this.loopDetector.addAndCheck(event)) {
      yield { type: GeminiEventType.LoopDetected };
      return turn;
    }
  }
  yield event;
}
```

---

## 5. UI 层处理

```typescript
function ChatMessage({ event }: { event: ServerGeminiStreamEvent }) {
  if (event.type === GeminiEventType.LoopDetected) {
    return (
      <Box color="yellow">
        <Text>⚠️ Loop detected. Stopping to prevent infinite loop.</Text>
        <Text dimColor>You can continue by sending a new message.</Text>
      </Box>
    );
  }
  // ...
}
```

---

## 6. 总结

Qwen Code 的循环检测特点：

1. **三层防护** - 工具重复、内容重复、语义检测
2. **渐进触发** - 短对话用轻量检测，长对话用 LLM 检测
3. **实时检测** - addAndCheck 在流式输出中实时检测
4. **可配置** - 支持 skipLoopDetection 禁用
5. **用户友好** - UI 提示循环检测触发
