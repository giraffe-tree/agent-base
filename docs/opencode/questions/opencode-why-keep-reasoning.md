# OpenCode 为何保留推理内容

**结论**: OpenCode 保留 `reasoning-delta` 推理内容是为了支持 **流式增量更新**和**上下文压缩决策**，使 LLM 在长时间运行的会话中保持高效的实时推理能力。

---

## 核心原因

### 1. 流式处理的实时性

```typescript
case "reasoning-delta":
  part.text += value.text
  await Session.updatePartDelta({...})
```

流式模式下：
- 推理内容**增量写入**数据库
- UI 可实时显示思考过程
- LLM 能基于**完整思考链**决定下一步

### 2. 上下文压缩的智能决策

```typescript
// SessionCompaction.isOverflow() 监控 token 使用量
// 自动压缩时保留推理内容的摘要有助于：
// - 判断哪些推理是关键的
// - 决定压缩策略
```

`filterCompacted()` 会回溯消息历史，**推理内容帮助判断哪些部分可以安全压缩**。

### 3. Doom Loop 防护

```typescript
// 检测连续三次相同工具调用
// 推理内容用于：
// - 分析为什么会重复调用
// - 触发权限确认时提供上下文
```

保留推理让系统能检测并打断无效的推理循环。

### 4. 长时间任务保护

`resetTimeoutOnProgress` 与推理内容配合：
- 推理进度作为"活动"标志重置超时
- 防止长推理任务被意外中断

---

## 技术实现

**关键代码**:
- `opencode/packages/opencode/src/session/processor.ts` - 流式处理
- `opencode/packages/opencode/src/session/message-v2.ts` - 消息存储
- `opencode/packages/opencode/src/session/compaction.ts` - 上下文压缩

---

*2026-02-21*
