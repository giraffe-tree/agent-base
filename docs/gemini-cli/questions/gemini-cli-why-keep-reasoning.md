# Gemini CLI 为何保留推理内容

**结论**: Gemini CLI 保留推理内容是为了支持 **Scheduler 状态机**的完整状态重建和**双轨历史系统**的会话恢复，使 LLM 在多轮工具调用调度中保持决策连贯性。

---

## 核心原因

### 1. Scheduler State Machine 需求

Scheduler 管理工具调用生命周期：
```
Validating → Scheduled → Executing → Success/Error
```

每个状态转换都依赖于 LLM 的推理过程。保留 `thinking_blocks` 让模型能：
- 回顾为什么发起某个工具调用
- 理解当前状态机的位置
- 在出错时基于原推理进行恢复

### 2. 双轨历史系统

```typescript
getHistory(curated: boolean = false): Content[] {
  const history = curated
    ? extractCuratedHistory(this.history)  // 过滤后
    : this.history;                        // 完整（含推理）
  return structuredClone(history);
}
```

**Comprehensive History** 保留完整推理，用于：
- 会话录制和恢复 (`ChatRecordingService`)
- 调试和审计
- 长期状态持久化

**Curated History** 可选移除，用于：
- 减少 token 消耗
- 传给模型的最终上下文

### 3. 嵌套 Agent 调用

通过 `parentCallId` 支持嵌套 Agent 时，保留推理内容让父 Agent 能理解子 Agent 的决策依据。

---

## 技术实现

**关键代码**:
- `gemini-cli/packages/core/src/scheduler/scheduler.ts` - 状态机
- `gemini-cli/packages/core/src/core/geminiChat.ts` - 双轨历史
- `gemini-cli/packages/core/src/services/chatRecordingService.ts` - 会话录制

---

*2026-02-21*
