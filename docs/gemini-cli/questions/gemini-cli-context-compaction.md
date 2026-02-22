# Gemini CLI 上下文压缩机制

## 是否支持

✅ **完整支持** - Gemini CLI 实现了两阶段验证压缩机制，配合 Reverse Token Budget 策略优化上下文利用率。

## 核心设计

**两阶段验证**: 先生成摘要，再验证摘要完整性；采用 Reverse Token Budget 从最新消息向后分配预算，优先保证近期上下文质量。

## 关键代码位置

| 文件路径 | 职责 |
|---------|------|
| `gemini-cli/packages/core/src/services/chatCompressionService.ts` | 压缩服务主入口 |
| `gemini-cli/packages/core/src/services/chatCompressionService.ts:80-150` | `compressChatHistory()` 核心流程 |
| `gemini-cli/packages/core/src/services/chatCompressionService.ts:180-250` | `generateSummary()` 摘要生成 |
| `gemini-cli/packages/core/src/services/chatCompressionService.ts:280-350` | `validateCompression()` 完整性验证 |
| `gemini-cli/packages/core/src/strategies/reverseTokenBudget.ts` | Reverse Token Budget 策略 |

## 压缩流程

```
触发条件
    │
    ├─► Token 超过 70% 可用上下文
    ├─► 工具输出超过 50K tokens
    └─► 用户手动触发 compress 命令
    │
    ▼
┌─────────────────┐
│  Reverse Budget │──► 从后向前分配 token 预算
│  Calculation    │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Smart Split    │──► 选择 30% 历史作为压缩候选
│  Point Selection│
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Phase 1:       │──► LLM 生成候选摘要
│  Generate       │
│  Summary        │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Phase 2:       │──► 验证摘要完整性
│  Validate       │──► Token 增加则放弃
└─────────────────┘
    │
    ▼
Replace or Keep
```

## 实现细节

### 1. Reverse Token Budget 策略

```typescript
// gemini-cli/packages/core/src/strategies/reverseTokenBudget.ts:45
interface TokenBudget {
  recentContextBudget: number;    // 最近消息预算 (50%)
  toolOutputBudget: number;       // 工具输出预算 (30%)
  summaryBudget: number;          // 摘要预算 (20%)
}

export function calculateReverseBudget(
  totalBudget: number,
  messageHistory: Message[]
): TokenAllocation {
  // 从最新消息开始，向后分配预算
  const allocation: TokenAllocation = {
    preservedIndices: [],
    compressionCandidates: [],
  };

  let remainingBudget = totalBudget;

  // 优先保留最近 30% 的消息
  const recentThreshold = Math.floor(messageHistory.length * 0.3);

  for (let i = messageHistory.length - 1; i >= 0; i--) {
    const msg = messageHistory[i];
    const msgTokens = estimateTokens(msg);

    if (messageHistory.length - 1 - i < recentThreshold) {
      // 最近 30% 强制保留
      allocation.preservedIndices.push(i);
      remainingBudget -= msgTokens;
    } else if (remainingBudget >= msgTokens) {
      allocation.preservedIndices.push(i);
      remainingBudget -= msgTokens;
    } else {
      allocation.compressionCandidates.push(i);
    }
  }

  return allocation;
}
```

### 2. 工具输出独立预算

Gemini CLI 对工具输出设置独立预算（默认 50K tokens）:

```typescript
// gemini-cli/packages/core/src/services/chatCompressionService.ts:120
const TOOL_OUTPUT_BUDGET = 50000;

function handleToolOutputOverflow(
  messages: Message[],
  toolOutputBudget: number
): CompressionDecision {
  const toolOutputs = messages.filter(m => m.type === 'tool_output');
  const totalToolTokens = toolOutputs.reduce(
    (sum, m) => sum + estimateTokens(m.content), 0
  );

  if (totalToolTokens > toolOutputBudget) {
    // 触发工具输出压缩
    return {
      action: 'compress_tool_outputs',
      targetIndices: selectToolOutputsForCompression(toolOutputs),
    };
  }

  return { action: 'no_action' };
}
```

### 3. 两阶段验证压缩

```typescript
// gemini-cli/packages/core/src/services/chatCompressionService.ts:180
async function compressWithValidation(
  messages: Message[],
  splitIndex: number
): Promise<CompressionResult> {
  // Phase 1: 生成摘要
  const candidateMessages = messages.slice(0, splitIndex);
  const summary = await generateSummary(candidateMessages);

  const compressedMessages = [
    { role: 'system', content: `[Conversation Summary]\n${summary}` },
    ...messages.slice(splitIndex),
  ];

  // Phase 2: 验证压缩效果
  const originalTokens = countTokens(messages);
  const compressedTokens = countTokens(compressedMessages);

  // 关键验证：压缩后 token 数必须减少
  if (compressedTokens >= originalTokens * 0.95) {
    logger.warn('Compression ineffective, discarding result');
    return {
      success: false,
      reason: 'insufficient_compression',
      keptMessages: messages,
    };
  }

  // 完整性验证：检查关键信息是否保留
  const validationResult = await validateSummary(summary, candidateMessages);
  if (!validationResult.isComplete) {
    logger.warn('Summary incomplete, adjusting compression boundary');
    return retryWithAdjustedBoundary(messages, splitIndex + 5);
  }

  return {
    success: true,
    messages: compressedMessages,
    savedTokens: originalTokens - compressedTokens,
  };
}
```

### 4. 完整性验证提示词

```typescript
// gemini-cli/packages/core/src/services/chatCompressionService.ts:300
const VALIDATION_PROMPT = `请验证以下摘要是否完整保留了原始对话的关键信息：

原始对话：
{{originalConversation}}

生成的摘要：
{{summary}}

请检查：
1. 用户的原始需求是否被准确描述？
2. 所有已执行的操作是否被记录？
3. 所有技术决策是否被保留？
4. 是否有遗漏的重要代码变更？

以 JSON 格式返回：
{
  "isComplete": boolean,
  "missingItems": string[],
  "confidenceScore": number
}`;
```

### 5. 智能分割点选择

```typescript
// gemini-cli/packages/core/src/services/chatCompressionService.ts:140
function selectOptimalSplitPoint(
  messages: Message[],
  budget: number
): number {
  // 避免在以下位置分割：
  const AVOID_PATTERNS = [
    /^(working|thinking|analyzing)/i,     // 思考中
    /^(writing|editing|creating)/i,        // 文件操作中
    /^(error|exception|failed)/i,          // 错误处理中
  ];

  let candidateIndex = Math.floor(messages.length * 0.3);

  // 向前/向后查找合适的分割点
  while (candidateIndex < messages.length * 0.7) {
    const msg = messages[candidateIndex];
    const isGoodSplitPoint = !AVOID_PATTERNS.some(p => p.test(msg.content));

    if (isGoodSplitPoint && isNaturalBoundary(msg)) {
      return candidateIndex;
    }
    candidateIndex++;
  }

  return Math.floor(messages.length * 0.3); // 默认 30%
}
```

## 设计权衡

### 优点

| 优势 | 说明 |
|------|------|
| **质量保证** | 两阶段验证确保压缩不会丢失关键信息 |
| **预算可控** | Reverse Token Budget 明确分配策略，可预测 |
| **工具友好** | 工具输出独立预算，避免大输出挤占对话空间 |
| **失败安全** | 压缩无效时自动放弃，避免劣化用户体验 |

### 缺点

| 劣势 | 说明 |
|------|------|
| **双重成本** | 生成摘要 + 验证完整性 = 两次 LLM 调用 |
| **验证延迟** | 完整性验证增加压缩总耗时 |
| **保守策略** | 验证失败时可能保留过多内容 |
| **配置复杂** | 多预算参数需要精细调优 |

### 与其他 Agent 对比

| 维度 | Gemini CLI | Codex | Kimi CLI |
|------|------------|-------|----------|
| **验证机制** | 两阶段验证 | 单次生成 | 无验证 |
| **预算策略** | Reverse Budget | 阈值触发 | 保留最近 N 条 |
| **工具处理** | 独立预算 | 统一处理 | 统一处理 |
| **失败处理** | 放弃压缩 | 渐进截断 | 强制压缩 |

### 适用场景

- ✅ 高质量要求的企业场景
- ✅ 工具调用频繁的开发任务
- ✅ 长对话需要精确控制 token 分配
- ⚠️ 成本敏感场景（双重 LLM 调用）
