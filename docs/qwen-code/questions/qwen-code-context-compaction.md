# 上下文压缩机制（Qwen Code）

本文深入分析 Qwen Code 的上下文压缩机制。

---

## 1. 压缩触发条件

✅ **Verified**: `qwen-code/packages/core/src/services/chatCompressionService.ts`

```typescript
// 默认阈值：70% 触发压缩
export const COMPRESSION_TOKEN_THRESHOLD = 0.7;

// 保留比例：最近 30%
export const COMPRESSION_PRESERVE_THRESHOLD = 0.3;

async compress(
  chat: GeminiChat,
  promptId: string,
  force: boolean,
  model: string,
  config: Config,
  hasFailedCompressionAttempt: boolean,
): Promise<{ newHistory: Content[] | null; info: ChatCompressionInfo }> {
  // 检查条件
  if (
    curatedHistory.length === 0 ||
    threshold <= 0 ||
    (hasFailedCompressionAttempt && !force)
  ) {
    return { newHistory: null, info: { compressionStatus: CompressionStatus.NOOP } };
  }

  // 检查 token 阈值
  const contextLimit = config.getContentGeneratorConfig()?.contextWindowSize ??
    DEFAULT_TOKEN_LIMIT;

  if (!force && originalTokenCount < threshold * contextLimit) {
    return { newHistory: null, info: { compressionStatus: CompressionStatus.NOOP } };
  }

  // 执行压缩...
}
```

---

## 2. 分割点计算

```typescript
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

---

## 3. 压缩执行流程

```
1. 获取 curatedHistory（过滤无效记录）
        │
        ▼
2. 计算 splitPoint（保留最近 30%）
        │
        ▼
3. 调用模型总结早期对话
   - 使用 generateContent API
   - Prompt: "Summarize the above conversation..."
        │
        ▼
4. 重建 history
   [summary_message, ...preserved_messages]
        │
        ▼
5. 返回压缩结果
   - 原始 token 数
   - 压缩后 token 数
   - 压缩状态
```

---

## 4. 边界条件处理

| 场景 | 处理方式 |
|------|----------|
| 历史为空 | 返回 NOOP |
| 压缩已失败过 | 跳过（除非 force=true）|
| 无合适的 split point | 使用 lastSplitPoint |
| 总结失败 | 标记 hasFailedCompressionAttempt |
| 最后一条是 functionCall | 不压缩全部 |

---

## 5. 压缩效果示例

```
压缩前：
- 消息数：100 条
- Token 数：90K（超过 70% of 128K）

压缩后：
- 消息数：31 条（1 条总结 + 30 条保留）
- Token 数：35K
- 压缩率：61%
```

---

## 6. 总结

Qwen Code 的上下文压缩特点：

1. **阈值触发** - 70% 自动触发，支持强制压缩
2. **智能分割** - 在 user message 边界分割
3. **模型总结** - 使用 LLM 生成对话摘要
4. **失败保护** - 失败标记避免重复尝试
5. **事件通知** - ChatCompressed 事件通知 UI
