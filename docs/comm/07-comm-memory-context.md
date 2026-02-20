# 记忆与上下文管理对比

## 1. 概念定义

**记忆（Memory）** 是 Agent 在会话过程中积累和保留的信息，包括对话历史、工具执行结果、文件状态等。

**上下文（Context）** 是每次模型调用时传递给 LLM 的完整信息集合，包括系统提示、历史消息、当前输入等。

### 核心挑战

- **上下文窗口限制**：LLM 有最大 token 限制
- **信息检索**：如何快速找到相关信息
- **状态持久化**：如何保存和恢复会话状态
- **压缩与摘要**：如何处理超长上下文

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 使用简单的列表存储历史记录，基于 token 数量进行窗口管理，无内置压缩机制。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Agent History (历史记录)                                 │
│  ├── list[dict]                    消息列表             │
│  │   ├── {"role": "system", ...}                        │
│  │   ├── {"role": "user", ...}                          │
│  │   ├── {"role": "assistant", ...}                     │
│  │   └── {"role": "user", ...}  (observation)           │
│  └── token_count                   估算 token 数        │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Window Management (窗口管理)                             │
│  ├── 滑动窗口                      保留最近 N 条        │
│  └── 系统提示始终保留              不受窗口影响         │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| History | `sweagent/agent/agents.py` | 250 | 历史记录 |
| Token Count | `sweagent/agent/models/` | - | Token 估算 |

**特点**

- 简单直接，易于理解
- 无压缩机制，依赖窗口滑动
- 适合短会话任务

### 2.2 Codex

**实现概述**

Codex 使用 SQLite 存储会话数据，支持 Conversation 和 Item 两层结构，无内置上下文压缩。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  SQLite Storage (存储层)                                  │
│  ┌─────────────────────────────────────────────────────┐│
│  │ conversations 表                                    ││
│  │ - id, created_at, updated_at                        ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ items 表                                            ││
│  │ - id, conversation_id, type, content                ││
│  │ - Type: message, function_call, function_output     ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Conversation Window (上下文窗口)                         │
│  ├── 加载最近 N 条 Item                               │
│  ├── 转换为 LLM 消息格式                              │
│  └── 发送到模型                                       │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Storage | `codex-rs/core/src/storage/sqlite.rs` | 1 | SQLite 存储 |
| Conversation | `codex-rs/core/src/conversation.rs` | 1 | 会话结构 |

**特点**

- 持久化存储，支持会话恢复
- SQL 查询能力
- 依赖模型级窗口管理

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 使用动态压缩机制，包括 `tryCompressChat` 和 `tryMaskToolOutputs`，支持会话级和 prompt 级状态管理。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  GeminiClient (会话管理)                                  │
│  ├── sessionTurnCount              会话轮次计数         │
│  ├── maxSessionTurns               最大轮次限制         │
│  └── chatHistory                   聊天历史             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Compression (压缩机制)                                   │
│  ├── tryCompressChat()             压缩历史             │
│  ├── tryMaskToolOutputs()          遮罩大输出           │
│  └── ContextWindowWillOverflow     溢出预警             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  IDE Context (可选)                                       │
│  └── 追加 editor context           代码上下文           │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Client | `packages/core/src/core/client.ts` | 80 | 会话管理 |
| Compression | `packages/core/src/core/compression.ts` | - | 压缩逻辑 |

**压缩策略**

- **智能压缩**：保留重要消息，移除冗余
- **输出遮罩**：隐藏大体积工具输出
- **溢出预警**：提前检测上下文溢出

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 提供最强大的上下文管理能力，包括 Checkpoint 回滚机制和显式压缩功能。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Context (上下文管理)                                     │
│  ├── history: list[Message]        消息历史             │
│  ├── token_count: int              Token 计数           │
│  └── checkpoints: list[Checkpoint] 检查点列表           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Checkpoint System (检查点系统)                           │
│  ├── checkpoint()                  创建检查点           │
│  ├── revert_to(id)                 回滚到检查点         │
│  └── BackToTheFuture               D-Mail 异常          │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Compaction (上下文压缩)                                  │
│  ├── compact_context()             压缩上下文           │
│  ├── 生成压缩消息                  LLM 生成摘要         │
│  └── 重建 checkpoint               新起点               │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Context | `kimi-cli/src/kimi_cli/context.py` | 1 | 上下文类 |
| Checkpoint | `kimi-cli/src/kimi_cli/checkpoint.py` | 1 | 检查点 |
| Compaction | `kimi-cli/src/kimi_cli/agent/soul.py` | 350 | 压缩逻辑 |

**D-Mail 机制**

```python
# 抛出异常触发回滚
raise BackToTheFuture(
    checkpoint_id=checkpoint_id,
    messages=[system_prompt, dmail_content]
)

# Agent Loop 捕获并处理
try:
    outcome = await self._step()
except BackToTheFuture as e:
    # 回滚到检查点
    self.context.revert_to(e.checkpoint_id)
    # 创建新检查点
    self.context.checkpoint()
    # 注入 D-Mail
    self.context.append(SystemMessage(content=e.messages))
```

### 2.5 OpenCode

**实现概述**

OpenCode 使用 SQLite 存储消息和 Part，提供 Compaction 和 Prune 两种上下文管理机制。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  SQLite DB (数据层)                                       │
│  ┌─────────────────────────────────────────────────────┐│
│  │ sessions 表                                         ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ messages 表                                         ││
│  │ - id, sessionID, type, finish                       ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ parts 表                                            ││
│  │ - id, messageID, type, status                       ││
│  │ - type: text, tool, reasoning                       ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Compaction (上下文压缩)                                  │
│  ├── SessionCompaction.process()   压缩处理             │
│  ├── compaction agent              无工具权限           │
│  └── 生成结构化摘要                目标/进展/文件       │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Prune (输出裁剪)                                         │
│  ├── SessionCompaction.prune()     裁剪旧输出           │
│  └── 保护受保护工具                skill 等             │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Session | `packages/opencode/src/session/session.ts` | 100 | 会话 |
| Message | `packages/opencode/src/session/message.ts` | 1 | 消息 |
| Compaction | `packages/opencode/src/session/compaction.ts` | 1 | 压缩 |

**Compaction 触发条件**

```typescript
// 已用 token >= (模型上下文窗口 - reserved buffer)
const isOverflow = () => {
    return usedTokens >= (maxContextWindow - reservedBuffer);
};

// 默认 reserved = min(20000, maxOutputTokens)
```

---

## 3. 相同点总结

### 3.1 通用结构

所有 Agent 都管理以下信息：

| 信息类型 | 说明 |
|----------|------|
| 系统提示 | Agent 的角色和行为定义 |
| 用户消息 | 用户的输入 |
| 助手响应 | LLM 的回复 |
| 工具调用 | 工具执行请求和结果 |
| Token 计数 | 上下文使用量估算 |

### 3.2 通用策略

| 策略 | 说明 |
|------|------|
| 窗口滑动 | 保留最近的消息，丢弃旧的 |
| 系统提示保护 | 始终保留系统提示 |
| Token 估算 | 估算上下文使用量 |

---

## 4. 不同点对比

### 4.1 存储方式

| Agent | 存储介质 | 持久化 | 查询能力 |
|-------|----------|--------|----------|
| SWE-agent | 内存 | 可选文件 | 无 |
| Codex | SQLite | 自动 | SQL |
| Gemini CLI | 内存 | 可选 | 无 |
| Kimi CLI | 内存 | 无 | 无 |
| OpenCode | SQLite | 自动 | SQL + 索引 |

### 4.2 上下文粒度

| Agent | 存储单位 | 粒度 | 特点 |
|-------|----------|------|------|
| SWE-agent | Message | 粗 | 简单 |
| Codex | Item | 中 | 类型化 |
| Gemini CLI | Content/Thought/ToolCall | 细 | 事件驱动 |
| Kimi CLI | Message | 中 | 可回滚 |
| OpenCode | Part | 最细 | 灵活 |

### 4.3 压缩机制

| Agent | 压缩方式 | 触发条件 | 特点 |
|-------|----------|----------|------|
| SWE-agent | 无 | - | 依赖窗口滑动 |
| Codex | 无 | - | 依赖模型窗口 |
| Gemini CLI | 动态压缩 | Token 阈值 | 智能压缩 |
| Kimi CLI | 显式压缩 | Token 阈值 | 生成摘要 |
| OpenCode | Compaction + Prune | Token 阈值 | 滑动窗口 |

### 4.4 状态恢复

| Agent | 回滚能力 | 实现方式 | 应用场景 |
|-------|----------|----------|----------|
| SWE-agent | 否 | - | - |
| Codex | 否 | - | - |
| Gemini CLI | 否 | - | - |
| Kimi CLI | 是 | Checkpoint | D-Mail |
| OpenCode | 否 | - | - |

### 4.5 Token 管理

| Agent | 估算方式 | 预留空间 | 溢出处理 |
|-------|----------|----------|----------|
| SWE-agent | 字符估算 | 无 | 截断 |
| Codex | 模型计算 | 无 | 依赖模型 |
| Gemini CLI | 模型计算 | 有 | 压缩 |
| Kimi CLI | 模型计算 | 有 | 压缩 |
| OpenCode | 模型计算 | 有 | compaction |

---

## 5. 源码索引

### 5.1 上下文定义

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/agent/agents.py` | 250 | history 列表 |
| Codex | `codex-rs/core/src/conversation.rs` | 1 | Conversation |
| Gemini CLI | `packages/core/src/core/client.ts` | 80 | chatHistory |
| Kimi CLI | `kimi-cli/src/kimi_cli/context.py` | 1 | Context |
| OpenCode | `packages/opencode/src/session/session.ts` | 100 | SessionV2 |

### 5.2 压缩/管理

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Gemini CLI | `packages/core/src/core/compression.ts` | 1 | tryCompressChat |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py` | 350 | compact_context |
| OpenCode | `packages/opencode/src/session/compaction.ts` | 1 | SessionCompaction |

### 5.3 存储/持久化

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/core/src/storage/sqlite.rs` | 1 | SQLite 存储 |
| OpenCode | `packages/opencode/src/db/` | - | Drizzle ORM |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 简单任务 | SWE-agent | 轻量，无复杂管理 |
| 会话恢复 | Codex/OpenCode | SQLite 持久化 |
| 长上下文 | Gemini CLI | 动态压缩 |
| 需要回滚 | Kimi CLI | Checkpoint 机制 |
| 细粒度控制 | OpenCode | Part 级别管理 |
| 上下文压缩 | Kimi CLI/OpenCode | 显式压缩策略 |
