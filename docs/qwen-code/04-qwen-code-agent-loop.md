# Agent Loop（Qwen Code）

本文基于 Qwen Code 源码实现，说明其如何把「模型流式输出 + 工具调用 + 工具结果回注 + 继续推理」组织成一个可控的 Agent Loop。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图

```text
┌─────────────────────────────────────────────────────────────────────┐
│  START: 用户输入                                                     │
│  ┌─────────────────────────────────────────┐                        │
│  │ submitQuery()                           │ ◄── UI 入口            │
│  │ (UI: useGeminiStream)                   │                        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Client 层: GeminiClient                                            │
│  ┌─────────────────────────────────────────┐                        │
│  │ sendMessageStream()                     │ ◄── 递归入口点         │
│  │  (packages/core/src/core/client.ts:403) │                        │
│  │  ├── 重置状态(loop detector等)          │                        │
│  │  ├── 检查 maxSessionTurns               │ ──► 超出则终止         │
│  │  ├── tryCompressChat()                  │ ──► 上下文压缩         │
│  │  ├── getIdeContextParts()               │ ──► IDE 上下文注入     │
│  │  └── turn.run()                         │                        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Turn 层: 单轮处理                                                   │
│  ┌─────────────────────────────────────────┐                        │
│  │ turn.run()                              │                        │
│  │ (packages/core/src/core/turn.ts:233)    │                        │
│  │  ├── chat.sendMessageStream()           │ ──► 调用 API           │
│  │  └── 流式产出:                          │                        │
│  │      ├── Content (文本)                 │ ──► UI 展示            │
│  │      ├── Thought (思考)                 │                        │
│  │      ├── ToolCallRequest (工具请求)     │ ──► 触发工具执行       │
│  │      └── Finished (完成)                │                        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  续跑判断: Continuation 递归                                         │
│  ┌─────────────────────────────────────────┐                        │
│  │ turn.run() 返回后:                      │                        │
│  │  ├── 有 pendingToolCalls?               │                        │
│  │  │   ├─Yes──► 调度工具执行             │                        │
│  │  │   │        └── 生成 functionResponse │                        │
│  │  │   │            │                    │                        │
│  │  │   │            ▼                    │                        │
│  │  │   │    ┌─────────────────┐         │                        │
│  │  │   └───►│ sendMessageStream│─────────┼──► 递归 (isContinuation)│
│  │  │        │ (续跑下一轮)     │         │      turns-1           │
│  │  │        └─────────────────┘         │                        │
│  │  │                                    │                        │
│  │  └── 无 pending tools                 │                        │
│  │      └── checkNextSpeaker()?          │                        │
│  │          ├── 'model' ──► 递归续跑     │                        │
│  │          └── 其他 ──► Finished 收敛    │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流
```

### 1.2 关键分支流程图

```text
┌──────────────────────────────────────────────────────────────────────┐
│ [A] Turn 执行状态机 —— 单轮事件流                                      │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │ turn.run() 开始  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ chat.sendMessage│
                    │    Stream()     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ 流式读取 chunks  │
                    └────────┬────────┘
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │   Content    │  │     Thought  │  │ ToolCallReq  │
   │   (文本增量)  │  │   (思考摘要)  │  │  (工具请求)  │
   └──────────────┘  └──────────────┘  └──────┬───────┘
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │ pendingToolCalls│
                                     │    .push()      │
                                     └─────────────────┘
                                              │
                             ┌────────────────┘
                             ▼
                    ┌─────────────────┐
                    │ finishReason?   │
                    │ (STOP/LENGTH/...)│
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ yield Finished  │
                    │ turn 结束       │
                    └─────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [B] Loop Detection 分支 —— 防止无限循环                               │
└──────────────────────────────────────────────────────────────────────┘

                         ┌─────────────────┐
                         │ turn 开始前      │
                         └────────┬────────┘
                                  │
                                  ▼
                         ┌─────────────────┐
                         │ loopDetector.   │
                         │ turnStarted()   │
                         └────────┬────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │Yes               │No                 │
              ▼                   ▼                   ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │  Loop Detected! │  │ 添加事件到历史   │  │ 检查 chanting   │
    │  ────────────── │  │ loopDetector.   │  │ (内容重复检测)  │
    │  终止递归链      │  │ addAndCheck()   │  └─────────────────┘
    └─────────────────┘  └─────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [C] 终止条件分支 —— 何时结束递归                                       │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │ sendMessageStream│
                    │ 终止条件检查     │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐
│  硬性限制      │   │  用户干预      │   │  正常收敛          │
├───────────────┤   ├───────────────┤   ├───────────────────┤
│ • MAX_TURNS   │   │ • 中断信号     │   │ • 无 pending tools│
│   (默认100)   │   │   (AbortSignal)│   │ • next_speaker≠   │
│ • maxSession  │   │ • 取消工具     │   │   'model'         │
│   Turns       │   │                │   │                   │
└───────┬───────┘   └───────┬───────┘   └─────────┬─────────┘
        │                   │                     │
        └───────────────────┼─────────────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │   终止递归链     │ ──► Finished
                   └─────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [D] 上下文压缩分支 —— Token 管理                                       │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │ sendMessageStream│
                    │ 调用前检查       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ tryCompressChat │
                    │ (压缩阈值 0.7)   │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │超出阈值且                     │未超出
              │未失败过                       │
              ▼                              ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ ChatCompressionSvc  │       │ 跳过压缩            │
    │ .compress()         │       │ CompressionStatus   │
    │                     │       │ .NOOP               │
    │ 1. 保留最近 30%      │       │                     │
    │ 2. 总结压缩 70%      │       │                     │
    │ 3. 生成 summary     │       │                     │
    └──────────┬──────────┘       └─────────────────────┘
               │
               ▼
    ┌─────────────────────┐
    │ yield ChatCompressed│
    │ 更新 chat history   │
    └─────────────────────┘


图例: 🔍 校验  ⚙️ 执行  ✅/❌/🚫 结果状态  🔄 循环检测
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道主循环是 `sendMessageStream` 递归驱动，核心在 `client.ts:403`。
- **3 分钟版**：看 `1.1` + `1.2` + `3` 节，了解 Turn 事件流、循环检测、终止条件。
- **10 分钟版**：通读全文，能定位递归续跑、工具执行、异常处理等问题。

### 2.1 一句话定义

Qwen Code 的 Agent Loop 是「**递归 continuation 驱动的模型-工具循环**」：`sendMessageStream` 递归调用处理多轮对话，每轮 Turn 流式解析模型输出，工具结果回注后继续递归，直到无 pending tools 或命中终止条件。

---

## 3. Agent Loop 核心实现

### 3.1 sendMessageStream 方法

✅ **Verified**: `qwen-code/packages/core/src/core/client.ts:403`

```typescript
async *sendMessageStream(
  request: PartListUnion,
  signal: AbortSignal,
  prompt_id: string,
  options?: { isContinuation: boolean },
  turns: number = MAX_TURNS,  // 默认 100
): AsyncGenerator<ServerGeminiStreamEvent, Turn> {
  // 非续跑时重置状态
  if (!options?.isContinuation) {
    this.loopDetector.reset(prompt_id);
    this.lastPromptId = prompt_id;
    this.stripThoughtsFromHistory();  // 清理思考内容
  }

  // 检查会话轮次限制
  this.sessionTurnCount++;
  if (this.config.getMaxSessionTurns() > 0 &&
      this.sessionTurnCount > this.config.getMaxSessionTurns()) {
    yield { type: GeminiEventType.MaxSessionTurns };
    return new Turn(this.getChat(), prompt_id);
  }

  // 确保 turns 不超过 MAX_TURNS
  const boundedTurns = Math.min(turns, MAX_TURNS);
  if (!boundedTurns) {
    return new Turn(this.getChat(), prompt_id);
  }

  // 尝试压缩上下文
  const compressed = await this.tryCompressChat(prompt_id, false);
  if (compressed.compressionStatus === CompressionStatus.COMPRESSED) {
    yield { type: GeminiEventType.ChatCompressed, value: compressed };
  }

  // 检查 token 限制
  const sessionTokenLimit = this.config.getSessionTokenLimit();
  if (sessionTokenLimit > 0) {
    const tokenCount = uiTelemetryService.getLastPromptTokenCount();
    if (tokenCount > sessionTokenLimit) {
      yield { type: GeminiEventType.SessionTokenLimitExceeded, ... };
      return new Turn(this.getChat(), prompt_id);
    }
  }

  // IDE 上下文注入（如果有 pending tool call 则跳过）
  if (this.config.getIdeMode() && !hasPendingToolCall) {
    const { contextParts } = this.getIdeContextParts(...);
    if (contextParts.length > 0) {
      this.getChat().addHistory({ role: 'user', parts: [...] });
    }
  }

  // 创建 Turn 并执行
  const turn = new Turn(this.getChat(), prompt_id);

  // 循环检测
  if (!this.config.getSkipLoopDetection()) {
    const loopDetected = await this.loopDetector.turnStarted(signal);
    if (loopDetected) {
      yield { type: GeminiEventType.LoopDetected };
      return turn;
    }
  }

  // 执行 Turn，流式产出事件
  const resultStream = turn.run(this.config.getModel(), requestToSent, signal);
  for await (const event of resultStream) {
    // 实时循环检测
    if (!this.config.getSkipLoopDetection()) {
      if (this.loopDetector.addAndCheck(event)) {
        yield { type: GeminiEventType.LoopDetected };
        return turn;
      }
    }
    yield event;
    if (event.type === GeminiEventType.Error) {
      return turn;
    }
  }

  // 检查是否需要续跑
  if (!turn.pendingToolCalls.length && signal && !signal.aborted) {
    if (this.config.getSkipNextSpeakerCheck()) {
      return turn;
    }

    const nextSpeakerCheck = await checkNextSpeaker(...);
    if (nextSpeakerCheck?.next_speaker === 'model') {
      const nextRequest = [{ text: 'Please continue.' }];
      // 递归续跑
      yield* this.sendMessageStream(
        nextRequest,
        signal,
        prompt_id,
        options,
        boundedTurns - 1,
      );
    }
  }
  return turn;
}
```

### 3.2 Turn.run 方法

✅ **Verified**: `qwen-code/packages/core/src/core/turn.ts:233`

```typescript
async *run(
  model: string,
  req: PartListUnion,
  signal: AbortSignal,
): AsyncGenerator<ServerGeminiStreamEvent> {
  try {
    const responseStream = await this.chat.sendMessageStream(
      model, { message: req, config: { abortSignal: signal } }, this.prompt_id
    );

    for await (const streamEvent of responseStream) {
      if (signal?.aborted) {
        yield { type: GeminiEventType.UserCancelled };
        return;
      }

      // 处理重试事件
      if (streamEvent.type === 'retry') {
        yield { type: GeminiEventType.Retry, retryInfo: streamEvent.retryInfo };
        continue;
      }

      const resp = streamEvent.value as GenerateContentResponse;
      this.debugResponses.push(resp);

      // 提取并产出思考内容
      const thoughtText = getThoughtText(resp);
      if (thoughtText) {
        yield { type: GeminiEventType.Thought, value: parseThought(thoughtText) };
      }

      // 产出文本内容
      const text = getResponseText(resp);
      if (text) {
        yield { type: GeminiEventType.Content, value: text };
      }

      // 处理工具调用请求
      const functionCalls = resp.functionCalls ?? [];
      for (const fnCall of functionCalls) {
        const event = this.handlePendingFunctionCall(fnCall);
        if (event) yield event;
      }

      // 检查完成原因
      const finishReason = resp.candidates?.[0]?.finishReason;
      if (finishReason) {
        this.finishReason = finishReason;
        yield {
          type: GeminiEventType.Finished,
          value: { reason: finishReason, usageMetadata: resp.usageMetadata },
        };
      }
    }
  } catch (e) {
    // 错误处理...
  }
}

private handlePendingFunctionCall(fnCall: FunctionCall): ServerGeminiToolCallRequestEvent | null {
  const callId = generateCallId();
  this.pendingToolCalls.push({
    callId,
    name: fnCall.name,
    args: fnCall.args as Record<string, unknown>,
    isClientInitiated: false,
    prompt_id: this.prompt_id,
    response_id: this.currentResponseId,
  });
  return {
    type: GeminiEventType.ToolCallRequest,
    value: { callId, name: fnCall.name, args: fnCall.args, ... },
  };
}
```

### 3.3 上下文压缩

✅ **Verified**: `qwen-code/packages/core/src/core/client.ts:178`

```typescript
private async tryCompressChat(
  promptId: string,
  force: boolean,
): Promise<{ compressionStatus: CompressionStatus; info: ChatCompressionInfo }> {
  // 未启用压缩或历史为空
  if (!this.config.getChatCompression()?.enabled) {
    return { compressionStatus: CompressionStatus.NOOP, info: {...} };
  }

  const compressionService = new ChatCompressionService();
  const { newHistory, info } = await compressionService.compress(
    this.getChat(),
    promptId,
    force,
    this.config.getModel(),
    this.config,
    this.hasFailedCompressionAttempt,
  );

  if (newHistory) {
    this.setHistory(newHistory);
  }

  if (info.compressionStatus === CompressionStatus.FAILED) {
    this.hasFailedCompressionAttempt = true;
  }

  return { compressionStatus: info.compressionStatus, info };
}
```

---

## 4. 循环检测机制

### 4.1 LoopDetectionService

✅ **Verified**: `qwen-code/packages/core/src/services/loopDetectionService.ts`

```typescript
export class LoopDetectionService {
  private turnCount = 0;
  private lastToolCalls: string[] = [];
  private consecutiveSameToolCalls = 0;

  constructor(private readonly config: Config) {}

  reset(promptId: string): void {
    this.turnCount = 0;
    this.lastToolCalls = [];
    this.consecutiveSameToolCalls = 0;
  }

  async turnStarted(signal: AbortSignal): Promise<boolean> {
    this.turnCount++;
    // 长对话后使用 LLM 进行语义循环检测
    if (this.turnCount > LONG_CONVERSATION_THRESHOLD) {
      return await this.performLlmLoopCheck(signal);
    }
    return false;
  }

  addAndCheck(event: ServerGeminiStreamEvent): boolean {
    // 检测连续相同工具调用
    if (event.type === GeminiEventType.ToolCallRequest) {
      const toolKey = `${event.value.name}:${JSON.stringify(event.value.args)}`;
      // 检查是否与最近工具调用重复
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
    return false;
  }
}
```

---

## 5. 终止条件

以下任一条件命中都将终止递归链：

| 条件 | 检查位置 | 说明 |
|------|----------|------|
| `boundedTurns <= 0` | `client.ts:430` | 达到 MAX_TURNS 限制 |
| `maxSessionTurns` 超出 | `client.ts:421` | 会话总轮次限制 |
| `sessionTokenLimit` 超出 | `client.ts:443` | Token 限制 |
| `signal.aborted` | `turn.ts:253` | 用户中断 |
| Loop Detected | `client.ts:491` | 循环检测触发 |
| `next_speaker !== 'model'` | `client.ts:558` | 无需模型继续 |

---

## 6. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 循环不终止 | 检查 MAX_TURNS 设置 | `client.ts:76` |
| 不触发工具 | 检查 Turn 事件解析 | `turn.ts:291` |
| 上下文溢出 | 检查压缩配置 | `client.ts:434` |
| 循环误报 | 检查 loopDetector 配置 | `loopDetectionService.ts` |
| 续跑失败 | 检查 checkNextSpeaker | `nextSpeakerChecker.ts` |
| IDE 上下文未注入 | 检查 hasPendingToolCall | `client.ts:468` |

---

## 7. 架构特点

### 7.1 递归 vs 循环

```typescript
// Qwen Code 使用递归而非 while 循环
// 优点:
// 1. 每轮有独立的 turns 计数
// 2. 事件流可以 yield 出来
// 3. 更容易处理异步工具执行

async *sendMessageStream(..., turns: number): AsyncGenerator<...> {
  // ... 当前轮处理 ...

  // 递归续跑
  yield* this.sendMessageStream(nextRequest, signal, prompt_id, options, turns - 1);
}
```

### 7.2 事件驱动架构

```typescript
// Turn 产出事件，而非直接操作
// 优势:
// 1. UI 可以实时响应
// 2. 支持取消和重试
// 3. 便于遥测和调试

for await (const event of turn.run(...)) {
  yield event;  // 透传给上层
}
```

### 7.3 工具调用队列

```typescript
// pendingToolCalls 队列管理
// 支持:
// 1. 并发工具调用
// 2. 工具结果回注
// 3. 取消处理

readonly pendingToolCalls: ToolCallRequestInfo[] = [];
```

---

## 8. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 递归驱动 | ✅ 支持 | ✅ 继承 |
| MAX_TURNS | 100 | ✅ 相同 |
| 循环检测 | 多层 | ✅ 继承 |
| 上下文压缩 | 0.7 阈值 | ✅ 继承 |
| IDE 注入 | 支持 | ✅ 增强 |
| NextSpeaker | 支持 | ✅ 继承 |

---

## 9. 总结

Qwen Code 的 Agent Loop 设计特点：

1. **递归 continuation** - sendMessageStream 自我递归驱动循环
2. **流式事件架构** - Turn 产出事件，支持实时响应
3. **多层循环检测** - 工具重复、语义检测、LLM 复核
4. **智能上下文管理** - 压缩、IDE 注入、token 限制
5. **完善的终止条件** - 硬性限制、用户干预、自然收敛
