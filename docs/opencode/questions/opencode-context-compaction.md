# OpenCode 上下文压缩机制

## 是否支持

✅ **双重机制** - OpenCode 实现了 Compaction（专用 Agent 生成结构化摘要）+ Prune（Part 级别裁剪）的双重上下文管理机制。

## 核心设计

**双重策略**: Compaction 由专用 Agent 生成结构化摘要（Goal/Instructions/Discoveries/Accomplished）；Prune 在 Part 级别裁剪，保护最近工具调用和 skill 输出。

## 关键代码位置

| 文件路径 | 职责 |
|---------|------|
| `opencode/packages/opencode/src/session/compaction.ts` | Compaction 主逻辑 |
| `opencode/packages/opencode/src/session/compaction.ts:60-120` | `compact()` 核心函数 |
| `opencode/packages/opencode/src/session/compaction.ts:150-220` | `CompactionAgent` 专用 Agent |
| `opencode/packages/opencode/src/session/prune.ts` | Prune 裁剪逻辑 |
| `opencode/packages/opencode/src/session/prune.ts:40-90` | `pruneParts()` 核心函数 |
| `opencode/packages/opencode/src/session/tokenManager.ts:180-250` | 自动触发判断 |

## 压缩流程

```
Token 监控
    │
    └─► 超过可用上下文 80% ?
    │
    ├─► Yes ──► Compaction 路径
    │              │
    │              ▼
    │         ┌─────────────────┐
    │         │  CompactionAgent│──► 生成结构化摘要
    │         │  (专用 Agent)   │    Goal/Instructions/
    │         └─────────────────┘    Discoveries/Accomplished
    │              │
    │              ▼
    │         ┌─────────────────┐
    │         │  Store as       │──► 存储为 CompactPart
    │         │  CompactPart    │
    │         └─────────────────┘
    │
    └─► Prune 路径（持续运行）
           │
           ▼
    ┌─────────────────┐
    │  Part Level     │──► 按 Part 粒度评估
    │  Evaluation     │
    └─────────────────┘
           │
           ▼
    ┌─────────────────┐
    │  Protect Recent │──► 保护最近 40K tokens
    │  40K + Skills   │    保护 skill 输出
    └─────────────────┘
           │
           ▼
    ┌─────────────────┐
    │  Remove Old     │──► 移除旧 Part
    │  Parts          │
    └─────────────────┘
```

## 实现细节

### 1. Compaction 机制

#### 专用 Compaction Agent

```typescript
// opencode/packages/opencode/src/session/compaction.ts:150
class CompactionAgent {
  private model: Model;
  private systemPrompt: string;

  constructor() {
    this.systemPrompt = `你是一个上下文压缩专家。
请将对话历史总结为以下结构化格式：

<goal>
用户想要达成的目标
</goal>

<instructions>
给 AI 的指令和约束条件
</instructions>

<discoveries>
任务过程中的重要发现，包括：
- 代码结构洞察
- 问题根因分析
- 解决方案评估
</discoveries>

<accomplished>
已完成的工作清单
</accomplished>

<relevant_files>
相关的文件路径及其当前状态
</relevant_files>

规则：
1. 保持客观，使用第三人称
2. 具体引用文件路径
3. 保留代码片段（如果关键）
4. 不要添加超出原文的信息`;
  }

  async generateSummary(messages: Message[]): Promise<CompactionSummary> {
    const response = await this.model.generate({
      system: this.systemPrompt,
      messages: messages,
      response_format: { type: "xml" },
    });

    return this.parseSummary(response.content);
  }
}
```

#### CompactPart 存储

```typescript
// opencode/packages/opencode/src/session/compaction.ts:60
interface CompactPart {
  type: "compact";
  id: string;
  created_at: number;
  summary: {
    goal: string;
    instructions: string;
    discoveries: string[];
    accomplished: string[];
    relevant_files: Array<{
      path: string;
      status: "created" | "modified" | "deleted" | "unchanged";
      description?: string;
    }>;
  };
  original_message_range: [number, number];
  token_savings: number;
}

async function compact(
  session: Session,
  options: CompactionOptions = {}
): Promise<CompactionResult> {
  const messages = session.getMessages();

  // 确定压缩范围（排除最近的交互）
  const preserveCount = options.preserveLastN ?? 4;
  const compactRange = messages.slice(0, -preserveCount);

  if (compactRange.length < 10) {
    return { performed: false, reason: "insufficient_history" };
  }

  // 使用专用 Agent 生成摘要
  const agent = new CompactionAgent();
  const summary = await agent.generateSummary(compactRange);

  const compactPart: CompactPart = {
    type: "compact",
    id: generateId(),
    created_at: Date.now(),
    summary,
    original_message_range: [0, compactRange.length],
    token_savings: estimateTokens(compactRange) - estimateTokens(summary),
  };

  // 替换原始消息
  session.replaceRange(
    [0, compactRange.length],
    [compactPart]
  );

  return {
    performed: true,
    compactPart,
    remainingMessages: messages.slice(-preserveCount),
  };
}
```

### 2. Prune 机制

#### Part 级别评估

```typescript
// opencode/packages/opencode/src/session/prune.ts:40
interface Part {
  id: string;
  type: "message" | "tool_call" | "tool_output" | "skill" | "compact";
  content: unknown;
  tokens: number;
  timestamp: number;
  importance?: number;  // 0-1，由内容分析得出
}

interface PruneConfig {
  protectedRecentTokens: number;  // 默认 40000
  protectSkillOutputs: boolean;   // 默认 true
  minImportanceThreshold: number; // 默认 0.3
}

function pruneParts(
  parts: Part[],
  targetTokenCount: number,
  config: PruneConfig
): PruneResult {
  const protectedIds = new Set<string>();

  // 1. 保护最近 40K tokens
  let recentTokens = 0;
  for (let i = parts.length - 1; i >= 0; i--) {
    const part = parts[i];
    if (recentTokens < config.protectedRecentTokens) {
      protectedIds.add(part.id);
      recentTokens += part.tokens;
    } else {
      break;
    }
  }

  // 2. 保护 skill 输出
  if (config.protectSkillOutputs) {
    for (const part of parts) {
      if (part.type === "skill") {
        protectedIds.add(part.id);
      }
    }
  }

  // 3. 按重要性和年龄排序，移除低重要性旧内容
  const removable = parts
    .filter(p => !protectedIds.has(p.id))
    .map(p => ({
      ...p,
      score: (p.importance || 0.5) * Math.exp(-0.001 * (Date.now() - p.timestamp)),
    }))
    .sort((a, b) => a.score - b.score);

  let currentTokens = parts.reduce((sum, p) => sum + p.tokens, 0);
  const removed: Part[] = [];

  for (const part of removable) {
    if (currentTokens <= targetTokenCount) break;
    removed.push(part);
    currentTokens -= part.tokens;
  }

  return {
    kept: parts.filter(p => !removed.some(r => r.id === p.id)),
    removed,
    tokenReduction: parts.reduce((sum, p) => sum + p.tokens, 0) - currentTokens,
  };
}
```

#### 重要性评分

```typescript
// opencode/packages/opencode/src/session/prune.ts:120
function calculateImportance(part: Part): number {
  let score = 0.5; // 基础分

  switch (part.type) {
    case "skill":
      score += 0.4; // skill 输出重要
      break;
    case "tool_output":
      // 工具输出根据内容判断
      if (contains(part.content, ["error", "failed", "exception"])) {
        score += 0.3; // 错误信息重要
      }
      if (contains(part.content, ["success", "created", "modified"])) {
        score += 0.2; // 成功操作较重要
      }
      break;
    case "message":
      // 用户指令重要
      if (part.role === "user") {
        score += 0.3;
      }
      break;
  }

  // 内容长度惩罚（过长内容可能细节过多）
  if (part.tokens > 10000) {
    score -= 0.1;
  }

  return Math.max(0, Math.min(1, score));
}
```

### 3. 自动触发机制

```typescript
// opencode/packages/opencode/src/session/tokenManager.ts:180
class TokenManager {
  private readonly COMPACTION_THRESHOLD = 0.8;  // 80% 触发 Compaction
  private readonly PRUNE_THRESHOLD = 0.9;       // 90% 触发 Prune

  async checkAndCompact(session: Session): Promise<void> {
    const usage = await this.getTokenUsage(session);
    const ratio = usage.used / usage.available;

    if (ratio > this.PRUNE_THRESHOLD) {
      // 紧急情况：先 Prune
      logger.warn("Token usage critical, pruning...");
      await this.prune(session, usage.available * 0.7);
    }

    if (ratio > this.COMPACTION_THRESHOLD) {
      // 执行 Compaction
      logger.info("Token usage high, compacting...");
      const result = await compact(session);
      if (result.performed) {
        logger.info(`Compaction saved ${result.compactPart.token_savings} tokens`);
      }
    }
  }
}
```

### 4. 压缩报告

```typescript
// opencode/packages/opencode/src/session/compaction.ts:220
function generateCompactionReport(
  result: CompactionResult,
  pruneResult?: PruneResult
): string {
  const lines = [
    "🗜️  Context Compaction Report",
    "",
    "Compaction:",
    result.performed
      ? `  ✅ Performed: ${result.compactPart.token_savings} tokens saved`
      : `  ⏭️  Skipped: ${result.reason}`,
  ];

  if (result.performed) {
    lines.push(
      `  📊 Original messages: ${result.compactPart.original_message_range[1]}`,
      `  📁 Relevant files: ${result.compactPart.summary.relevant_files.length}`,
      `  ✅ Accomplished: ${result.compactPart.summary.accomplished.length} items`
    );
  }

  if (pruneResult) {
    lines.push(
      "",
      "Prune:",
      `  🗑️  Removed parts: ${pruneResult.removed.length}`,
      `  💾 Token reduction: ${pruneResult.tokenReduction}`
    );
  }

  return lines.join("\n");
}
```

## 设计权衡

### 优点

| 优势 | 说明 |
|------|------|
| **双重保障** | Compaction + Prune 应对不同场景 |
| **专用 Agent** | Compaction Agent 生成高质量结构化摘要 |
| **细粒度控制** | Part 级别裁剪，保护关键内容 |
| **Skill 保护** | 自动保护 skill 工具输出，避免技能失效 |

### 缺点

| 劣势 | 说明 |
|------|------|
| **双重成本** | Compaction Agent 调用 + Part 评估开销 |
| **复杂度较高** | 需要维护 Part 和 CompactPart 两种抽象 |
| **40K 保护固定** | 保护阈值硬编码，不够灵活 |
| **重要性启发式** | 基于规则的重要性评分可能不够准确 |

### 与其他 Agent 对比

| 维度 | OpenCode | Gemini CLI | Codex |
|------|----------|------------|-------|
| **机制** | Compaction + Prune | 两阶段验证 | LLM 摘要 + 截断 |
| **粒度** | Part 级别 | Message 级别 | Message 级别 |
| **Agent 专用** | 是 | 否 | 否 |
| **结构化** | XML 结构化 | 纯文本 | 纯文本 |
| **工具保护** | Skill 特殊保护 | 独立预算 | 无特殊处理 |

### 适用场景

- ✅ 频繁使用 skill 工具的场景
- ✅ 需要结构化摘要的长任务
- ✅ Part 粒度有明确区分的复杂会话
- ⚠️ 简单对话可能过度设计
