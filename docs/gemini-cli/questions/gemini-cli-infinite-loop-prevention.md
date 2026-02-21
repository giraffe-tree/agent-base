# Gemini CLI 如何避免 Tool 无限循环调用

**结论先行**: Gemini CLI 通过**多层循环检测** + **Final Warning Turn 优雅恢复** + **最大轮次硬性限制**防止 tool 无限循环。其核心创新是 `LoopDetectionService`，结合**工具调用哈希检测**、**内容重复检测**和**LLM-based 智能检测**三层机制。

---

## 1. LoopDetectionService 三层检测

位于 `gemini-cli/packages/core/src/services/loopDetectionService.ts`：

### 1.1 工具调用重复检测

```typescript
const TOOL_CALL_LOOP_THRESHOLD = 5;  // 相同工具调用 5 次即判定为循环

private checkToolCallLoop(toolCall: { name: string; args: object }): boolean {
  // 使用 SHA256 哈希工具调用名称和参数
  const key = this.getToolCallKey(toolCall);

  if (this.lastToolCallKey === key) {
    this.toolCallRepetitionCount++;
  } else {
    this.lastToolCallKey = key;
    this.toolCallRepetitionCount = 1;
  }

  // 相同工具调用达到 5 次，触发循环检测
  if (this.toolCallRepetitionCount >= TOOL_CALL_LOOP_THRESHOLD) {
    logLoopDetected(this.config, new LoopDetectedEvent(
      LoopType.CONSECUTIVE_IDENTICAL_TOOL_CALLS,
      this.promptId,
    ));
    return true;
  }
  return false;
}

private getToolCallKey(toolCall: { name: string; args: object }): string {
  const argsString = JSON.stringify(toolCall.args);
  const keyString = `${toolCall.name}:${argsString}`;
  return createHash('sha256').update(keyString).digest('hex');
}
```

**检测逻辑**:
- 对工具调用名称和参数进行 SHA256 哈希
- 连续相同哈希达到 5 次即判定为循环
- 支持交替模式（如 A→B→A→B）也会被检测

### 1.2 内容流重复检测（Chanting）

```typescript
const CONTENT_LOOP_THRESHOLD = 10;    // 内容块重复 10 次
const CONTENT_CHUNK_SIZE = 50;        // 每块 50 字符
const MAX_HISTORY_LENGTH = 5000;      // 最大历史长度

private checkContentLoop(content: string): boolean {
  // 跳过代码块内的重复（代码常有重复结构）
  const numFences = (content.match(/```/g) ?? []).length;
  if (numFences % 2 !== 0) {
    this.inCodeBlock = !this.inCodeBlock;
  }
  if (this.inCodeBlock) return false;

  this.streamContentHistory += content;
  this.truncateAndUpdate();
  return this.analyzeContentChunksForLoop();
}

private analyzeContentChunksForLoop(): boolean {
  while (this.hasMoreChunksToProcess()) {
    const currentChunk = this.streamContentHistory.substring(
      this.lastContentIndex,
      this.lastContentIndex + CONTENT_CHUNK_SIZE,
    );
    const chunkHash = createHash('sha256').update(currentChunk).digest('hex');

    if (this.isLoopDetectedForChunk(currentChunk, chunkHash)) {
      logLoopDetected(this.config, new LoopDetectedEvent(
        LoopType.CHANTING_IDENTICAL_SENTENCES,
        this.promptId,
      ));
      return true;
    }
    this.lastContentIndex++;
  }
  return false;
}
```

**防误报设计**:
- 代码块内的重复不检测（`inCodeBlock` 标志）
- 表格、列表、标题等结构化内容触发重置
- 使用滑动窗口 + 哈希聚类算法

### 1.3 LLM-based 智能检测

```typescript
const LLM_CHECK_AFTER_TURNS = 30;      // 30 轮后开始检测
const DEFAULT_LLM_CHECK_INTERVAL = 3;  // 每 3 轮检测一次
const LLM_CONFIDENCE_THRESHOLD = 0.9;  // 置信度阈值 0.9

private async checkForLoopWithLLM(signal: AbortSignal): Promise<boolean> {
  // 获取最近 20 轮对话历史
  const recentHistory = this.config.getGeminiClient()
    .getHistory()
    .slice(-LLM_LOOP_CHECK_HISTORY_COUNT);

  // 使用专用模型进行循环检测
  const flashResult = await this.queryLoopDetectionModel(
    'loop-detection',
    contents,
    signal,
  );

  const flashConfidence = flashResult['unproductive_state_confidence'] as number;

  // 置信度低于 0.9，不认为是循环
  if (flashConfidence < LLM_CONFIDENCE_THRESHOLD) {
    this.updateCheckInterval(flashConfidence);
    return false;
  }

  // 双重检查：使用主模型再次确认
  const mainModelResult = await this.queryLoopDetectionModel(
    DOUBLE_CHECK_MODEL_ALIAS,
    contents,
    signal,
  );
  const mainModelConfidence = mainModelResult?.['unproductive_state_confidence'] as number;

  if (mainModelConfidence >= LLM_CONFIDENCE_THRESHOLD) {
    this.handleConfirmedLoop(mainModelResult, doubleCheckModelName);
    return true;
  }

  return false;
}
```

**双模型验证**:
1. **Flash 模型**快速初筛
2. **主模型**双重确认（置信度均 ≥ 0.9）
3. 动态调整检测间隔（`MIN_LLM_CHECK_INTERVAL=5` 到 `MAX_LLM_CHECK_INTERVAL=15`）

---

## 2. 最大轮次限制（MAX_TURNS）

位于 `gemini-cli/packages/core/src/core/client.ts`：

```typescript
const MAX_TURNS = 100;  // 单会话最大 100 轮

private checkTermination(): boolean {
  if (this.sessionTurnCount >= MAX_TURNS) {
    this.handleFinalWarningTurn();
    return true;
  }
  return false;
}
```

---

## 3. Final Warning Turn 优雅恢复

### 3.1 实现机制

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
- 不直接中断对话，给 LLM 总结的机会
- 禁用工具防止继续循环
- 优雅地结束会话而非异常退出

---

## 4. 检测流程图

```
┌─────────────────────────────────────────────────────────────────┐
│              Gemini CLI Tool 调用防循环流程                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   每轮对话开始                                                   │
│        │                                                        │
│        ▼                                                        │
│   ┌───────────────────┐                                        │
│   │ turns >= 100?    │────────是────▶ Final Warning Turn       │
│   └───────────────────┘                  (禁用工具，要求总结)     │
│          │否                                                    │
│          ▼                                                      │
│   ┌───────────────────┐                                        │
│   │ turns >= 30 &    │────────否───▶ 正常执行                   │
│   │ interval 到达?    │                                        │
│   └───────────────────┘                                        │
│          │是                                                    │
│          ▼                                                      │
│   ┌───────────────────┐                                        │
│   │ LLM-based 检测    │                                        │
│   │ (Flash + 主模型)  │                                        │
│   └─────────┬─────────┘                                        │
│             │                                                   │
│      ┌──────┴──────┐                                           │
│      ▼              ▼                                           │
│   置信度<0.9      置信度≥0.9                                     │
│      │              │                                           │
│      ▼              ▼                                           │
│   调整间隔       触发循环处理                                     │
│   继续执行       (提示用户/终止)                                  │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                    流式输出阶段                          │   │
│   │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │   │
│   │  │ Tool Call   │───▶│ 相同调用?   │───▶│ 计数+1      │  │   │
│   │  └─────────────┘    └─────────────┘    └──────┬──────┘  │   │
│   │                                               │≥5?      │   │
│   │                                               ▼         │   │
│   │                                          触发循环检测    │   │
│   │  ┌─────────────┐    ┌─────────────┐                 │   │
│   │  │ Content     │───▶│ 内容哈希    │───▶│ 聚类分析    │  │   │
│   │  └─────────────┘    └─────────────┘    └──────┬──────┘  │   │
│   │                                               │≥10?     │   │
│   │                                               ▼         │   │
│   │                                          触发循环检测    │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 循环类型定义

```typescript
enum LoopType {
  CONSECUTIVE_IDENTICAL_TOOL_CALLS = 'consecutive_identical_tool_calls',
  CHANTING_IDENTICAL_SENTENCES = 'chanting_identical_sentences',
  LLM_DETECTED_LOOP = 'llm_detected_loop',
}
```

| 类型 | 触发条件 | 检测层 |
|------|---------|--------|
| `CONSECUTIVE_IDENTICAL_TOOL_CALLS` | 相同工具调用 5 次 | 工具调用层 |
| `CHANTING_IDENTICAL_SENTENCES` | 内容块重复 10 次 | 内容流层 |
| `LLM_DETECTED_LOOP` | LLM 置信度 ≥ 0.9 | 语义层 |

---

## 6. 与其他 Agent 的对比

| 防护机制 | Gemini CLI | Codex | Kimi CLI | OpenCode | SWE-agent |
|---------|------------|-------|----------|----------|-----------|
| **工具调用哈希检测** | ✅ 5次触发 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **内容流重复检测** | ✅ 滑动窗口 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **LLM-based 检测** | ✅ 双模型验证 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **最大轮次限制** | ✅ 100轮 | ✅ 有 | ✅ 100轮 | ✅ Infinity | ✅ 无限制 |
| **优雅恢复** | ✅ Final Warning | ❌ 无 | ✅ Checkpoint | ❌ 无 | ✅ Autosubmit |

---

## 7. 总结

Gemini CLI 的防循环设计哲学是**"智能检测 + 优雅恢复"**：

1. **三层检测**: 工具调用层（哈希）、内容流层（滑动窗口）、语义层（LLM）
2. **双模型验证**: Flash 快速初筛 + 主模型双重确认，降低误报
3. **动态间隔**: 根据置信度动态调整检测频率（5-15轮）
4. **优雅降级**: Final Warning Turn 给 LLM 总结机会，而非强制中断

Gemini CLI 是目前 5 大 Agent 中循环检测机制最完善的，其 LLM-based 检测可以识别复杂的语义级循环（如反复修改同一文件的不同位置但无实质进展）。

---

*文档版本: 2026-02-21*
*基于代码版本: gemini-cli (baseline 2026-02-08)*
